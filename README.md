<div align="center">

```
 ██████╗ ███████╗███████╗██████╗ ███████╗███████╗███████╗██╗  ██╗
 ██╔══██╗██╔════╝██╔════╝██╔══██╗██╔════╝██╔════╝██╔════╝██║ ██╔╝
 ██║  ██║█████╗  █████╗  ██████╔╝█████╗  █████╗  █████╗  █████╔╝
 ██║  ██║██╔══╝  ██╔══╝  ██╔═══╝ ██╔══╝  ██╔══╝  ██╔══╝  ██╔═██╗
 ██████╔╝███████╗███████╗██║     ███████╗███████╗███████╗██║  ██╗
 ╚═════╝ ╚══════╝╚══════╝╚═╝     ╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝
```

**An autonomous multi-tool CLI agent that runs entirely on DeepSeek.**

DeepSeek login · Instant/Expert/Vision modes · Tools · RAG · Memory
· Markdown skills · Termux-native

</div>

---

## What it is

DeepSeek-Agent takes a goal, plans the steps, calls the tools it needs
(files, shell, python, web, knowledge, memory), verifies its own work, retries
or replans on failure, and synthesises a final answer — without stopping to ask
you at every step.

It is built for **Termux on Android**: pure-Python core, no Docker, no heavy SDK,
no compiled dependency beyond optional numpy (plus `nodejs` for DeepSeek's PoW solver).

### The thing that matters most: it does not stop

An autonomous agent that dies on a 401 or 429 is useless. DeepSeek-Agent has **token-auto-refresh** and retries:

```
 1. TOKEN AUTO-REFRESH   token expired → re-login with saved credentials → fresh token
 2. STREAM RETRY         transient drop → retried automatically
 3. NATIVE MODES         instant / expert / vision (switch with /mode)
```

Every switch is reported to you. Credentials stay on-device (chmod 600).

---

## Install

**One command is all you need — setup.sh does the rest:**

```bash
git clone https://github.com/Saurabh-gzp/deepseek-agent.git && cd deepseek-agent
bash setup.sh
```

What `setup.sh` does:
1. **System packages** — auto-detects Termux/Linux/macOS and installs them
2. **Python deps** — `rich`, `PyYAML`, `numpy`, `prompt_toolkit`
3. **Old-install cleanup** — removes any previously saved keys/config, so the
   fresh clone stores its own keys in this directory
4. **`deepseek` command** — removes any pre-existing `nexus`/`deepseek` command/alias and installs its own launcher
5. **Self-test** — verifies the core imports
6. **Prints the launch command**

Then launch the agent — just one word, from anywhere:

```bash
deepseek
```

`setup.sh` installs this command for you: it removes any pre-existing
`nexus`/`deepseek` command or alias and installs its own launcher
(`python3 deepseek.py` still works inside the repo folder too).

On the first run a **login wizard** opens by itself — enter your **DeepSeek**
account email + password (the official chat.deepseek.com account). The agent
logs in, stores the token with chmod 600 **only on your device**, and
**auto-refreshes it when it expires**. Login is AWS-WAF aware (falls back to
a pasted token when a browser is unavailable, e.g. Termux).

### Slash autocomplete (`/` menu)

Typing `/` at the prompt lists every command with hints. Disable with `DEEPSEEK_FANCY_INPUT=0` or `ui.fancy_input: false` if a terminal reprints the prompt on resize.

### Optional notes

The default input is rock-stable everywhere. If you want `/`-command
autocomplete and arrow-key history, enable the fancy input:

```bash
export DEEPSEEK_FANCY_INPUT=1        # or set ui.fancy_input: true in config/config.yaml
```

If your terminal ever reprints the prompt on screen resize, keep it off.

### Update to the latest version

```bash
git pull && bash setup.sh --update
```

`--update` re-installs dependencies but **keeps your keys and config** (a plain
`bash setup.sh` wipes old keys/config for a completely fresh setup).

---

## Run

```bash
deepseek                                  # interactive REPL
deepseek "build me a todo API"            # one-shot
deepseek -m never "fix the bug"           # full autonomy, no confirmations
deepseek -w ~/projects/myapp              # point at an existing project
```

| Flag | Meaning |
|---|---|
| `-w, --workspace` | working directory the agent may touch |
| `-m, --mode` | `smart` (default) · `always` (confirm everything) · `never` (YOLO) |
| `-t, --theme` | `cyber` · `matrix` · `mono` |
| `-q, --quiet` | hide step-by-step tool output |

---

## Architecture

```
                       User goal
                           │
                   ┌───────▼────────┐
                   │ SAFETY (mod.)  │  mistral-moderation
                   └───────┬────────┘
                   ┌───────▼────────┐
                   │ ROUTER  3B     │  trivial? → answer directly, done
                   └───────┬────────┘
                   ┌───────▼────────┐
                   │ SUPERVISOR     │  mistral-medium → task DAG
                   │  medium        │  replans on failure
                   └───────┬────────┘
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼          (max 3 parallel)
  ┌───────────┐     ┌────────────┐     ┌────────────┐
  │ RESEARCHER│     │ WORKER ×3  │     │   CODER    │
  │  small    │     │ ministral8b│     │devstral /  │
  │ web + OCR │     │ general    │     │codestral   │
  └─────┬─────┘     └─────┬──────┘     └─────┬──────┘
        └──────────────────┼──────────────────┘
                   ┌───────▼────────┐
                   │ CRITIC medium  │  reads files, RUNS code, scores 0-100
                   └───────┬────────┘
                     fail? │ retry / replan
                   ┌───────▼────────┐
                   │ mistral-large  │  hard cases only (1 call per task)
                   └───────┬────────┘
                   ┌───────▼────────┐
                   │ MEMORY + RAG   │  save summary, index outcome
                   └───────┬────────┘
                        Final answer
```

### Execution loop
```
PLAN → DAG → ASSIGN → RUN SAFE TASKS IN PARALLEL → COLLECT
     → VERIFY → failed? REPLAN/RETRY → success? SAVE MEMORY → RESPOND
```

### Model roles (v1.5 — capability-aware)
| Role | Model | Job |
|---|---|---|
| router (decider) | `mistral-small-2603` | intent classification + capability routing (task_type / model_hint), trivial answers |
| supervisor | `mistral-medium-2508` | planning, coordination, synthesis, per-task model pinning |
| worker | `mistral-small-2603` | general execution, data shaping, device queries |
| worker (deep) | `ministral-14b-2512` | worker fallback — extra reasoning when needed |
| researcher | `mistral-small-2603` | web research, live info (weather/news/prices), citations |
| coder (quick) | `codestral-2508` | small/single-file code edits, quick scripts |
| coder (repo) | `devstral-2512` | full repository tasks, bug fixes, website/UI implementation |
| critic | `mistral-medium-2508` | verification, scoring (tool-failure aware) |
| hard fallback | `mistral-large-2512` | difficult final checks (rate-limited to 1/task) |
| memory / RAG | `mistral-embed-2312` | embeddings |
| vision | `pixtral-12b-2409` | `see_image` — reads actual pixels (not in /v1/models, still served) |
| documents | `mistral-ocr-latest` | PDF/image extraction via /v1/ocr |
| safety | `mistral-moderation-2603` | input/output moderation |

> Models come from `config/config.yaml` → `models:` — that block is the source of truth;
> this table mirrors v1.10.5.


Every role has a fallback chain — see `config/config.yaml`. The supervisor can
also pin an exact model per task (validated whitelist); the plan table shows it.

> **Why router is 8B now:** the 3B router used to misroute (claiming actions it
> could not perform, answering device questions with "no access"). The 8B
> decider understands intent AND model capability mapping, so plans are
> assigned by capability: code/bug-fixes → coder models, web/live → researcher,
> device queries → worker + `device_info`.
>
> **Rate limits in `config.yaml` now match the real org limits**
> (admin.mistral.ai → Limits): embed 1.0 rps, small 0.83, 8B 3.13, 3B 12.5,
> codestral 2.08, devstral 0.83, large 0.07, medium ~0.38–0.5.
> Verified live on 2026-08-26: 56 models exist; `mistral-large-2512` hangs on
> some org keys (>180s → demoted to 2nd fallback); `labs-leanstral-1-5-1` and
> `glm-5-2` return 403 (org not enabled).

---

## Skills

Skills are markdown playbooks with **3-level progressive disclosure**: the agent always
sees a one-line description (~60 tokens), loads the full body only when the task matches,
and reads reference files only when it needs them.

```
skills/
├── plan/make_plan.md
├── web_development/frontend_ui_ux_design.md
├── web_development/backend_api_development.md
├── automation/webautomation/web_automation.md
├── coding/python_project_structure.md
├── coding/debugging_and_testing.md
├── research/deep_research.md
├── data/data_analysis.md
├── devops/termux_environment.md
└── content/technical_writing.md
```

DeepSeek-Agent also **pre-matches** skills to each task and instructs the agent to load the best
one first, so expertise is applied even when the model would have skipped it.

Add your own — create the file and it is live immediately:
```markdown
---
name: My Skill
description: What it does AND when to use it (this is the trigger).
tags: [keyword]
agents: ["coder"]
---
# Skill: My Skill
## When to use
## Procedure
## Checklist
## Anti-patterns
```
See `skills/README.md` for the authoring guide.

---

## RAG

SQLite + numpy hybrid search (dense cosine + keyword) — no Postgres, no Qdrant needed.

```
/index ./docs          index a folder
/index report.pdf      index a file
/rag                   stats
/forget-index          wipe
```
The workspace auto-indexes on startup, and every agent can call `search_knowledge`.
Task outcomes are indexed too, so past work is retrievable in later sessions.

---

## Memory

SQLite-backed: sessions, messages, task summaries, facts and preferences.
Context is built from a **recent-message window + semantic search**, never a raw dump.

```
/remember tone=concise
/memory
/sessions
/resume a1b2c3d4e5f6
```

---

## Safety

| Layer | What it does |
|---|---|
| Moderation | `mistral-moderation` on user input and final output |
| Sandbox | file writes confined to the workspace |
| Shell guard | `rm -rf /`, `mkfs`, fork bombs, pipe-to-shell blocked outright |
| Risk classes | every tool tagged `read_only` / `write` / `network` / `execute` / `destructive` |
| Least privilege | router has no tools; critic is read-only + execute; researcher cannot delete |
| Approval gate | delete, deploy, email, publish, payments, account changes ask you first |
| Deletion choke-point | file deletion is hard-blocked inside `run_shell`/`run_python` — only `delete_path` (with approval) can delete |
| Denied-path freeze | deny a file once and no tool (rename/move/write) touches that path again |
| Router guard | action requests are never answered directly by the router — they always go to the supervisor |

Budgets that stop runaway loops (`config/config.yaml`):
```yaml
max_subagents: 5          max_parallel_agents: 3
max_task_depth: 3         max_steps_per_agent: 12
max_retries: 2            task_timeout_seconds: 180
overall_timeout_seconds: 900
large_model_calls_per_task: 1
```

---

## API keys: kitne chahiye taaki 429 kabhi na ruke (multi-key pools)

Mistral 429 storms (live run: dozens of `rate-limited -> cooling` events) are
absorbed by the KeyRing. **No artificial rotation cap**: every key you add
participates in rotation; when all keys cool, the ring waits (≤45s) and retries
the soonest — the agent pauses briefly, it never dies.

**Keys add karne ke 3 tarike** (sab ek saath chalte hain):
```bash
export MISTRAL_APIS="key1,key2,key3,..."        # ek hi var me 10+ keys (best)
# ya numbered:
export MISTRAL_API_KEY_1="..." ... MISTRAL_API_KEY_10="..."
# ya CLI:  deepseek keys add <key>
```

**Hisaab (kitni keys?)** — limits per API key (free/Experiment tier: ~1 rps +
500k tok/min per key; paid tiers zyada). Ek run me peak concurrency ≈ 4 streams
(3 parallel tasks + critic), har stream ~0.2-0.5 rps → **peak ≈ 1.5-3 rps**.
Isliye:

| Keys (alag accounts) | Aggregate capacity | Result |
|---|---|---|
| 1-2 | ~1-2 rps | bina margin — storms par pause (jaise live run me) |
| 4-6 | ~4-6 rps | stable; shayad hi kabhi wait |
| **10+** | **~10+ rps** | **kabhi nahi rukega** (3-5× margin) |

Rule of thumb: `ceil(peak_rps / per_key_rps) + 2` = **5-6 minimum, 10 = safe**.
🗝️ **Same account ki multiple keys vs alag accounts:** ho sake to **alag accounts**
use karein — per-org quotas (e.g. tok/min, monthly) alag accounts pe fully stack
karte hain; same org ki keys sirf per-key line help karti hain.

## What's new in v1.7.0 — web search that never gives up

- **7 search engines with health-aware rotation**: DuckDuckGo HTML/Lite → Bing →
  SearXNG → Mojeek → Wikipedia → DDG instant-answer. Root cause fixed: DDG
  rate-limits after ~2-3 rapid queries (anomaly page), which used to kill the
  whole research leg — now a blocked engine is demoted 120s and the others pick
  up the query (Bing is independent and keeps working during a DDG block).
- **Query cache (600s)** — re-running the same query costs 0 engine hits.
- **Result merging + dedup** across engines; bing `ck/a` and ddg `uddg`
  redirects unwrapped; ad results dropped.
- **web_fetch hardening**: mobile UA → desktop UA → reader-proxy fallback.
- **deep_research skill §0 (mandatory)**: plain 2-4 keyword queries only (never
  `site:`/operators — they return nothing), simplify-once-then-move-on, never
  re-run the same query, write the report after 2+ confirmed sources. Live:
  "Claude AI frontend design" research now fetches Anthropic's own pages.

## What's new in v1.6.0 — sutra-style discipline (no wrong commands)

- `device_info` is now pure-Python + `which()`-guarded: it reports REAL values
  or explicit `unavailable + fix-hint` — the agent never guesses commands
  ("network status check": 409s/186k tok/17 failures → 34s/7.5k tok/0 failures).
- The model sees an **AVAILABLE COMMANDS** fact block, so `termux-*`, `adb`,
  `dumpsys` guesses are impossible; 3 consecutive tool failures force a HALT
  and honest finalization (no more 200k-token burn loops).
- **Capability is enforced in the harness**: design/UI/code/website tasks are
  always coder (devstral/codestral) — the 8B worker can never be assigned
  coding work, whatever the plan says.
- `start_server` — hosting is started + verified (HTTP 200 + content marker) in
  one call and stays up; "hosted" claims are now proven.
- web_search falls back across 4 engines, so blocked DuckDuckGo no longer
  kills research.

## What's new in v1.5.0 — capability-aware autonomy

- **8B capability decider** — router now classifies intent AND which model class
  should do the work (`task_type` + `model_hint`); the supervisor's DAG follows.
- **Per-task model pinning** — the plan can name the exact model
  (`codestral-2508` for a quick script, `devstral-2512` for a repo task);
  the frontend plan shows agent + model, backend keeps the detail.
- **`device_info` tool** — one-shot correct Termux device report (storage via
  `~/storage/*`, battery, network, memory). "storage info" went from
  **251 s / 144,732 tokens / 5 failed `du -sh /sdcard/*` runs** to
  **~85 s / ~16 k tokens / 0 failed runs** (measured live).
- **Critic is tool-failure aware** — a task whose tool calls errored can no
  longer be certified 100-pass; it is capped at partial (≤79) unless the critic
  re-verifies the data itself (live bug: 5 failed runs still scored 100.0).
- **run_shell / run_python never raise** — every exception becomes a
  `ToolResult` error; the agent loop cannot be killed by a tool bug.
- **Bug fixes:** all-digit session-id prefix `/resume` (was ~3.7% of sessions),
  `test_tui_session.py` SyntaxError + hardcoded ROOT, `test_live.py` stale
  project-isolation paths. Tests: 134 offline + 26/26 live.

## What's new in v1.4.2 — "workspace clean" fix + full tool audit

Six root causes found from a live Termux bug report — all fixed and
verified live:

- **Deletion actually happens now** — the worker agent previously had no
  `delete_path` access at all and just asked the user for a "YES" in text.
  Now one approval (`a` = always) runs the whole delete batch — live proof:
  5 files, 1 prompt, critic 100.0 pass, workspace completely empty,
  **no manual `rm -rf`**.
- **Delete-only goals no longer create a project folder** — `workspace clean`
  no longer pollutes `projects/<slug>/`.
- **Critic-fail ≠ done** — after retries, unverified work is honestly marked
  `FAILED`; `partial` acceptance only at score ≥ 60.
- **Tool errors are visible in the UI** (`↳ reason` under `✕`) — no silent
  failures.
- **Critic is project-scope aware** — root-location false conflicts gone;
  build tasks are ~2.5× faster than before.
- **Tool suite verified 22/22 by direct execution** — fs/search/shell/python/
  web/skills/memory/RAG + hard-blocked rm/python deletes. Tests: 128 pass.

## What's new in v1.4.1 — chat quality (screenshot feedback)

Live screenshot bug: typing "hy" triggered a 20s pipeline that created a
`goal_statement.md` (a clarification file!). Now:

* **"hy" → 0.4s instant reply** — zero LLM calls for greetings
  (deterministic warm replies, always in the user's script)
* **"what's your name" → instant DeepSeek-Agent intro** — with a capability list;
  internal ROUTER/SUPERVISOR names never leak
* **Memory pollution fix** — short inputs (greetings) never get stale context
  ("hy" + an old hosting memory used to become a "hosting follow-up" plan)
* **Vague goals no longer create files** — the supervisor just asks a friendly
  question directly (like the big agents); no absurd `goal_statement.md`
* **`deepseek ❯[/]` artifact fixed** — the prompt is now a clean `deepseek ❯`
* Live info (weather/news/prices) now comes from the web via the researcher —
  the "check weather.com" deflection is gone

## What's new in v1.4

1. **First-run setup wizard** — run `deepseek` once (without a key) → it asks:
   pick a provider → paste a key → the key is **verified LIVE** (invalid keys
   are never saved) → "another key?" — then straight to work.
2. **`/key` is now smart:**
   - `a` add → **duplicate check** (across both keys/ and .env) + **auto-verify**
     against that provider's API — invalid keys are rejected
   - `t` → key-select menu with **`all` on top** — test every key at once
   - One table for all keys with a **source** column (keys/ or .env)
3. The zip now contains **no API keys** — add your own (wizard or /key).

## What's new in v1.3

1. **`/key` — interactive key manager** 🔑 — menu: providers → keys → `a` add /
   `d N` delete / `t N` **live-test** (verified against the API) / `b` back / `0` exit.
   Keys are saved in `keys/<provider>.json` (chmod 600, gitignored).
   The old `.deepseek/keys.json` auto-migrates. Env keys and file keys both work.
2. **Slash autocomplete** — type `/` and every command appears with a hint;
   `/skill <tab>` completes skill ids, `/agent <tab>` names, `/mode <tab>`
   modes. Arrow keys browse history (persisted too).
   (`pip install prompt_toolkit` — setup.sh already installs it; without it
   a simple input fallback is used.)

## What's new in v1.2 (from live Termux feedback)

1. **No more calculator lies** — questions like `8282+282282` are now solved
   locally without the LLM (the router once claimed 601144). `CALC` phase = exact.
2. **Device questions are now actually checked** — ask about battery/storage/
   wifi/network and the agent probes with `system_info` + shell
   (`/sys/class/power_supply`, Termux `termux-battery-status`).
   The "I don't have access" lie is gone.
3. **Project isolation** — every build goal gets its own folder
   (`workspace/projects/<slug>/`). New projects never mix. List them with `/projects`.
4. **Live processing indicator** — every LLM call shows a `thinking · worker · 14s`
   spinner+timer. The screen never looks frozen.
5. **Cocoa theme (default)** 🤎 — agent work in dim brown; user messages/RESULT/PLAN
   keep their original colors. (`-t cyber` restores the old look)
6. **Frontend skill is now enforced** — 12 quality gates (`:root` tokens, `@media`,
   `:focus-visible`, 44px tap targets, 120+ lines of CSS...) which the critic checks
   via grep; thin/lazy UI fails verification.

## Testing & real-world audit

### Offline unit tests (101 tests, no API needed)
```bash
python3 -m pytest tests/test_core.py -q
```

### Real interactive TUI test (pty session — runs like a user terminal)
```bash
python3 tests/test_tui_session.py          # full: banner, chat, build task, approval y/n + deny
python3 tests/test_tui_session.py fast     # smoke commands only
```

### Live-audited safety (proven with adversarial pty sessions)
In real TUI runs the agent attempted these evasions — all blocked by deterministic harness rules:

| Evasion (caught live) | Defence (no trust in the model) |
|---|---|
| Router claimed "Deleted!" (it had no tools) | `router_guard()` — action requests are never answered directly |
| Files landed in `workspace/workspace/` (doubled path) | `_resolve()` dedup — relative and absolute |
| After a deny, renamed to `.deleted` via `move_path` to circumvent | **denied-path freeze** — no tool runs on denied targets |
| `os.system("rm")`, `shred`, `find -delete`, `.trash` move inside `run_python` | **deletion choke-point** — deletion hard-blocked in run_shell/run_python; only `delete_path` deletes (with human approval) |
| Stale session memory planned the wrong file (`todos.json`!) | Planning gets preferences only, never task summaries |

### See it all in one round
```bash
deepseek
deepseek ❯ /keys
deepseek ❯ hello, who are you?                 # instant reply
deepseek ❯ /auto Create squares.py that prints 1-10 squares, run it, save output to squares.txt
deepseek ❯ delete squares.txt permanently      # approval panel — press 'n' and the file survives
```

## Commands


```
/help                    /status              /keys [add <key>]
/skills [query]          /skill <id>          /tools
/rag                     /index <path>        /forget-index
/memory                  /remember k=v        /sessions   /resume <id>
/plan <goal>             /auto <goal>         /agent <name> <task>
/cd <path>               /mode smart|always|never
/verbose                 /clear               /exit
```

---

## Adding a provider

1. Enable it in `config/config.yaml`:
```yaml
providers:
  groq:
    enabled: true
    type: openai_compatible
    base_url: "https://api.groq.com/openai/v1"
    env_keys: ["GROQ_API_KEY", "GROQ_API_KEY_2"]
```
2. Point roles at its models under `models:`.

Any OpenAI-compatible endpoint (Groq, Together, OpenRouter, Ollama, LM Studio) works with
zero code. For a bespoke API, subclass `BaseProvider` and register it in
`deepseek_agent/providers/registry.py`.

---

## Project structure

```
deepseek-agent/
├── deepseek.py                 launcher
├── setup.sh                    one-command setup (deps + purge + self-test)
├── config/config.yaml          all configuration
├── skills/                     markdown playbooks
├── workspace/                  where the agent builds things
├── tests/
│   ├── test_core.py            175 offline unit tests
│   └── test_live.py            live API integration tests
└── deepseek_agent/
    ├── cli/          app.py (REPL), ui.py (rich terminal UI)
    ├── core/         config.py, context.py, jsonutil.py
    ├── providers/    keyring.py, mistral.py, deepseek.py, registry.py
    ├── llm/          client.py (roles, rate limiting, fallback)
    ├── agents/       base.py (ReAct loop), specialists.py (6 agents)
    ├── orchestrator/ dag.py (scheduler), engine.py (autonomous loop)
    ├── tools/        base.py, filesystem.py, shell.py, web.py
    ├── rag/          engine.py, store.py
    ├── memory/       store.py
    ├── skills/       loader.py
    └── safety/       guard.py
```

---

## Tests

```bash
python -m pytest tests/ -q      # 72 offline tests, ~5s, no API calls
python tests/test_live.py       # 26 live checks against the real API
```

---

## Termux notes

- `termux-wake-lock` before long runs, or Android kills the process.
- Settings → Battery → Termux → **Unrestricted**.
- `pkg install python-numpy` rather than `pip install numpy`.
- Playwright/Chromium do not run on Termux — the web automation skill covers the workarounds.
- `Killed` means OOM: reduce `max_parallel_agents` to 2.

---

## License

MIT
