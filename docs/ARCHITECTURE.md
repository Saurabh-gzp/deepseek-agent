# DeepSeek-Agent Architecture

Design notes for anyone extending the system.

---

## 1. Layer map

```
┌──────────────────────────────────────────────────────────────┐
│ CLI          deepseek_agent/cli/app.py · ui.py                        │  REPL, commands, rich UI
├──────────────────────────────────────────────────────────────┤
│ ORCHESTRATOR deepseek_agent/orchestrator/engine.py · dag.py           │  plan→run→verify→replan
├──────────────────────────────────────────────────────────────┤
│ AGENTS       deepseek_agent/agents/base.py · specialists.py           │  ReAct loop, 6 roles
├──────────────────────────────────────────────────────────────┤
│ CONTEXT      deepseek_agent/core/context.py                           │  DI container
│  ├─ TOOLS    tools/{filesystem,shell,web,base}.py            │  registry + risk classes
│  ├─ SKILLS   skills/loader.py                                │  progressive disclosure
│  ├─ RAG      rag/{engine,store}.py                           │  sqlite + numpy hybrid
│  ├─ MEMORY   memory/store.py                                 │  sessions, facts, tasks
│  └─ SAFETY   safety/guard.py                                 │  moderation + approvals
├──────────────────────────────────────────────────────────────┤
│ LLM          llm/client.py                                   │  roles, rate limit, fallback
├──────────────────────────────────────────────────────────────┤
│ PROVIDERS    providers/{keyring,mistral,openai_compat,registry}.py │ keys + HTTP
└──────────────────────────────────────────────────────────────┘
```

Dependencies point **downward only**. An agent never touches HTTP; a provider never
knows what a task is.

---

## 2. Resilience: why the agent never stops

Three independent layers, tried in order on every model call.

### Layer 1 — key rotation (`providers/keyring.py`)

Each key is a state machine:

```
HEALTHY ──401/403──> DEAD     (hard_fail_cooldown, then revived and retried)
   │  ▲
 429/5xx│  │ success
   ▼  │
COOLING ──cooldown elapsed──> HEALTHY
```

- `Retry-After` is honoured when the server sends it, otherwise the backoff grows with
  consecutive failures (4s, 8s, …) capped at `cooldown_seconds`.
- `acquire()` round-robins over available keys.
- **`acquire_or_wait()`** is the critical one: if *every* key is cooling it sleeps until
  the soonest recovers instead of raising. If the shortest wait is longer than `max_wait`
  it forces the least-bad key back into service. It returns `None` only when the ring is
  literally empty.
- Every transition calls the notifier, which the UI renders as a 🔑 line — the user always
  knows a switch happened.

### Layer 2 — model fallback (`llm/client.py`)

Each role has a chain in `config.yaml`:
```yaml
supervisor: {model: mistral-medium-latest, fallback: [mistral-small-2603, ministral-8b-2512]}
```
On failure the client walks the chain and tells the user which fallback is now in use.

### Layer 3 — provider fallback

`ProviderRegistry.order()` puts the default provider first, then every other enabled one.
When all of a provider's models fail, the loop moves to the next provider transparently.

### Rate limiting

`RateLimiter` reserves the next slot under a lock (rather than sleeping while holding it),
so N parallel agents queue instead of bursting. Configured RPS is paced at ~85% and a 429
pushes that model's next slot out globally via `penalise()`.

---

## 3. The autonomous loop (`orchestrator/engine.py`)

```
handle(goal)
 ├─ moderate input                    → block or continue
 ├─ build memory context              → recent window + semantic recall
 ├─ ROUTER (small-2603)                       → trivial? answer directly and return
 ├─ SUPERVISOR.plan()                 → JSON task DAG, sanitised, cycles broken
 └─ loop until settled or replans exhausted
     ├─ dag.ready(max_parallel)       → deps satisfied, parallel_safe respected
     ├─ ThreadPoolExecutor            → run the batch
     │   └─ _run_task (per task)
     │       ├─ attempt 1..max_retries+1, bounded by task time budget
     │       ├─ agent.run()           → ReAct loop with that agent's tools
     │       ├─ CRITIC.verify()       → reads files, RUNS code, scores 0-100
     │       ├─ pass/≥70              → DONE
     │       ├─ fail + actionable fix → retry with the critic's instructions
     │       ├─ fail, no actionable   → accept (no point burning tokens)
     │       └─ last resort           → hard_verify with mistral-large (1/task)
     ├─ failed tasks?                 → SUPERVISOR.plan(failure_note) and rebuild the DAG,
     │                                   carrying completed work forward as context
     └─ SUPERVISOR.synthesize()       → final answer
 ├─ moderate output
 └─ save session + task summary to memory (and index it for RAG)
```

### Guards against runaway loops
| Budget | Default | Enforced in |
|---|---|---|
| `max_parallel_agents` | 3 | thread pool size |
| `max_steps_per_agent` | 12 | `BaseAgent.run` |
| `max_retries` | 2 | `_run_task` |
| `task_timeout_seconds` | 180 | per agent run + per-task cumulative budget |
| `overall_timeout_seconds` | 1500 | `_execute_dag` |
| `large_model_calls_per_task` | 1 | `LLMClient._allow_large` |

---

## 4. Agent loop (`agents/base.py`)

Extended ReAct: **think → act → observe → reflect**.

- The system prompt is composed per task: identity → environment → skill catalog →
  pre-matched skill directive → RAG context → rules.
- Tool specs are filtered by `agent_name`, so the model only *sees* tools it may use
  (schema-level gating, not just prompt instructions).
- Every tool call passes through `AgentContext.approve()` before execution.
- Every `reflection_every` steps a checkpoint message asks the agent to state progress and
  remaining steps — this measurably reduces aimless looping.
- On budget exhaustion the agent is asked for a wrap-up answer rather than being cut off,
  so partial work is never lost.

### The critic
The most failure-prone component, so it is defended twice:
1. Its prompt explicitly lists the tools it has and forbids "I cannot execute" excuses
   (it previously hallucinated tool limitations and failed valid work).
2. `_parse` uses `core/jsonutil.extract_json` — a brace-balancing, string-aware,
   repair-then-parse extractor — and falls back to prose inference plus tool-success
   evidence. A malformed verdict never fails a good task.

---

## 5. Skills (`skills/loader.py`)

Three levels, per the Anthropic Agent Skills pattern:

| Level | What | Cost |
|---|---|---|
| 1 | YAML frontmatter in the system prompt | ~60 tokens per skill |
| 2 | Body, via `load_skill(id)` | ~1–4k tokens, on demand |
| 3 | `references/`, `scripts/` next to the file, via `read_file` | 0 until read |

Skill id = path relative to `skills/` without `.md`, so nesting is free:
`automation/webautomation/web_automation`.

**Pre-matching:** models often ignore a passive catalog, so `build_system()` runs a
token-overlap search against the task and injects a "⚑ Required first step: call
load_skill(...)" directive. This is what makes skills actually get used.

---

## 6. RAG (`rag/`)

- **Store:** SQLite table `chunks(id, collection, source, text, meta, embedding BLOB)`.
  Embeddings are float32 blobs; search loads them into a normalised numpy matrix.
- **Hybrid scoring:** `0.82 × cosine + 0.18 × keyword overlap`.
- **Chunking:** structure-aware — splits on markdown headings and paragraph boundaries
  before falling back to fixed windows with overlap.
- **Idempotent:** chunk id is `sha1(source:index:text)`, and re-indexing a source deletes
  its old chunks first. `has_source(path, mtime)` skips unchanged files.
- **Graceful degradation:** if embeddings fail, indexing still stores the text and
  retrieval falls back to `keyword_search`. RAG never blocks a run.
- Collections separate `default` (documents) from `memory` (task outcomes, facts).

Swapping in Qdrant/pgvector means implementing `add`/`search`/`delete_source` on a class
with the same shape and passing it to `RAGEngine`.

---

## 7. Tools & safety

Every tool declares a risk class and an agent allowlist:

| Risk | Examples | Default policy |
|---|---|---|
| `read_only` | read_file, list_dir, search_knowledge | always allowed |
| `write` | write_file, edit_file | allowed; confirmed in `always` mode |
| `network` | web_search, web_fetch, http_request | allowed |
| `execute` | run_shell, run_python, install_package | allowed; confirmed in `always` mode |
| `destructive` | delete_path | **always** needs approval |

Least privilege by role:
```
router     → no tools
critic     → read-only + run_shell/run_python (needs to verify by executing)
researcher → web + read-only + write_file + index_knowledge
worker     → read/write + python + web
coder      → everything including shell and delete
supervisor → planning, read, write, memory (no delete)
```

Three enforcement points: schema gating (the model never sees forbidden tools),
`ToolRegistry.execute` (rejects on mismatch), and `SafetyGuard.classify_action`
(maps a call onto approval actions like `deploy_production` by inspecting arguments).

Filesystem writes resolve through `_resolve()`, which rejects anything outside the
workspace root. Shell commands are matched against a dangerous-pattern list before
execution.

---

## 8. Extension points

| Want to add | Do this |
|---|---|
| A provider | Subclass `BaseProvider`, register in `providers/registry.py`, enable in config |
| An OpenAI-compatible endpoint | Config only: `type: openai_compatible` + `base_url` |
| A tool | `reg.add(name, description, json_schema, handler, Risk.X, agents=[...])` |
| An agent | Subclass `BaseAgent`, set `role_key`/`allowed_tools`/`system_prompt`, add to `AGENT_CLASSES` |
| A skill | Drop a `.md` with frontmatter into `skills/<category>/` |
| A vector DB | Implement the `VectorStore` interface, inject into `RAGEngine` |

---

## 9. Known constraints

- Playwright/Chromium is unavailable on Termux — the web automation skill documents the
  API-first workarounds.
- `mistral-large` is limited to 4 RPM, hence the 1-call-per-task budget.
- Parallelism above 3 tends to trigger 429s on a small key pool; add keys before raising it.
- Thread-based concurrency (not asyncio) keeps the codebase readable and is sufficient for
  I/O-bound model calls at this scale.
