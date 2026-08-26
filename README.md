<div align="center">

```
 ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
 ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
 ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
 ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
 ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
 ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
```

**An autonomous multi-agent CLI that runs on your phone.**

Supervisor · Router · Researcher · Workers · Coder · Critic
· Multi-key failover · RAG · Markdown skills · Termux-native

</div>

---

## What it is

Nexus takes a goal, plans a task DAG, assigns specialised sub-agents, runs them in
parallel, verifies every result with a critic agent, retries or replans on failure, and
synthesises a final answer — without stopping to ask you at every step.

It is built for **Termux on Android**: pure-Python core, no Docker, no heavy SDK,
no compiled dependency beyond optional numpy.

### The thing that matters most: it does not stop

An autonomous agent that dies on a 401 or 429 is useless. Nexus has **three layers of
resilience** on every single model call:

```
 1. KEY ROTATION      key #1 → 429 → cooling → key #2 (you are told, run continues)
 2. MODEL FALLBACK    mistral-medium → mistral-small → ministral-8b
 3. PROVIDER FALLBACK mistral → openai / groq / ollama (if configured)
```

If every key is rate-limited it waits for the soonest one to recover instead of failing.
Keys that return 401 are quarantined and revived later. Every switch is reported to you.

---

## Install

**Ek hi command chahiye — baaki sab setup.sh khud karega:**

```bash
git clone https://github.com/Saurabh-gzp/nexus-agent.git && cd nexus-agent
bash setup.sh
```

`setup.sh` kya-kya karta hai:
1. **System packages** — Termux/Linux/macOS auto-detect karke install
2. **Python deps** — `rich`, `PyYAML`, `numpy`, `prompt_toolkit`
3. **Purani install cleanup** — agar pehle se saved keys/config mili to delete
   (taaki naya clone apni fresh keys khud se is directory me save kare)
4. **Self-test** — core import verify
5. **Launch command bata deta hai**

Setup ke baad agent launch karo:

```bash
python3 nexus.py
```

Pehle run pe key wizard khud khul jata hai — apni **Mistral AI** key paste karo
(free: [console.mistral.ai](https://console.mistral.ai)) aur bas. Key `keys/`
folder me chmod 600 ke saath **sirf tumhare device par** save hoti hai.

More keys = more uptime — runtime pe `/keys add sk-...` se kabhi bhi jodo.

---

## Run

```bash
python nexus.py                          # interactive REPL
python nexus.py "build me a todo API"    # one-shot
python nexus.py -m never "fix the bug"   # full autonomy, no confirmations
python nexus.py -w ~/projects/myapp      # point at an existing project
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

### Model roles
| Role | Model | Job |
|---|---|---|
| router | `ministral-3b-2512` | classification, triage, trivial answers |
| supervisor | `mistral-medium-latest` | planning, coordination, synthesis |
| worker | `ministral-8b-2512` | general execution (×3 parallel) |
| researcher | `mistral-small-2603` | web research, documents, citations |
| coder (quick) | `codestral-2508` | small code generation |
| coder (repo) | `devstral-2512` | full repository tasks |
| critic | `mistral-medium-latest` | verification, scoring |
| hard fallback | `mistral-large-2512` | difficult final checks (rate-limited to 1/task) |
| memory / RAG | `mistral-embed-2312` | embeddings |
| documents | `mistral-ocr-latest` | PDF/image extraction |
| safety | `mistral-moderation-2603` | input/output moderation |

Every role has a fallback chain — see `config/config.yaml`.

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
├── automation/make_automation_script/web_automation.md
├── coding/python_project_structure.md
├── coding/debugging_and_testing.md
├── research/deep_research.md
├── data/data_analysis.md
├── devops/termux_environment.md
└── content/technical_writing.md
```

Nexus also **pre-matches** skills to each task and instructs the agent to load the best
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
| Deletion choke-point | `run_shell`/`run_python` me file-deletion hard-block — sirf `delete_path` (approval ke saath) se delete ho sakta hai |
| Denied-path freeze | jis file ka deletion aapne MANA kiya, us path par koi bhi tool (rename/move/write) nahi chalta |
| Router guard | action requests kabhi router ke direct-answer se nipat nahi hote — supervisor hi jaata hai |

Budgets that stop runaway loops (`config/config.yaml`):
```yaml
max_subagents: 5          max_parallel_agents: 3
max_task_depth: 3         max_steps_per_agent: 12
max_retries: 2            task_timeout_seconds: 180
overall_timeout_seconds: 900
large_model_calls_per_task: 1
```

---

## What's new in v1.4.2 — "workspace clean" fix + full tool audit

Live Termux bug-report se 6 root causes mile, sab fix + live-verified:

- **Delete ab sach me hota hai** — pehle worker ke paas `delete_path` tha hi
  nahi, wo text me "user se YES maango" likh baithta tha. Ab ek approval (`a`
  = always) ka poora delete-batch smoothly chalta hai — live proof: 5 files,
  1 prompt, critic 100.0 pass, workspace bilkul empty, **no manual `rm -rf`**.
- **Delete-only goals pe project-folder nahi banta** — `workspace clean kr`
  ab `projects/<slug>/` pollution nahi karta.
- **Critic-fail ≠ done** — retries ke baad unverified kaam honestly `FAILED`
  dikhata hai, `partial`-acceptance sirf score ≥ 60 par.
- **Tool errors UI me visible** (`✕` ke neeche `↳ reason`) — silent fail nahi.
- **Critic project-scope aware** — root-location false conflicts khatam,
  build tasks pehle se ~2.5× fast.
- **Tool-suite 22/22 direct-run verified** — fs/search/shell/python/web/
  skills/memory/RAG + rm/python-delete hard-block. Tests: 128 pass.

## What's new in v1.4.1 — chat quality (screenshot feedback)

Live screenshot bug: "hy" likhne par 20s ka pipeline chala aur `goal_statement.md`
ban gayi (clarification file!). Ab:

* **"hy" → 0.4s instant Hinglish reply** — greeting pe LLM call hi zero hai
  (deterministic warm replies, hamesha user ki script me)
* **"tumhara naam kya hai" → instant Nexus intro** — capability list ke saath;
  internal ROUTER/SUPERVISOR ka naam kabhi leak nahi hota
* **Memory pollution fix** — chhote inputs (greetings) ko router purana context
  nahi milta ("hy" + purana hosting context = "hosting follow-up" ban jata tha)
* **Vague goal pe file NAHI banti** — supervisor ab seedha friendly sawaal
  poochta hai (bade agents jaisa), `goal_statement.md` jaisi absurd files nahi
* **`nexus ❯[/]` artifact fix** — prompt ab bilkul clean `nexus ❯`
* Live-info (mausam/news/price) ab researcher ke through web se aata hai —
  "check weather.com" wala deflection gone

## What's new in v1.4

1. **First-run setup wizard** — pehli baar `nexus` chalao (bina key ke) → khud
   poochega: provider select karo → key paste karo → **key LIVE verify** hoti hai
   (invalid save hi nahi hoti) → "aur ek key?" — aur seedha kaam shuru.
2. **`/key` ab smart:**
   - `a` add → **duplicate check** (keys/ + .env dono se) + **auto-verify**
     ushi provider ke API se — invalid key reject
   - `t` → key-select menu with **`all` on top** — sabhi keys ek saath test
   - Ek hi table me sab keys with **source** column (keys/ ya .env)
3. Zip me ab **koi API key nahi** — apni khud add karo (wizard ya /key).

## What's new in v1.3

1. **`/key` — interactive key manager** 🔑 — menu: providers → keys → `a` add /
   `d N` delete / `t N` **live-test** (API se verify) / `b` back / `0` exit.
   Keys ab `keys/<provider>.json` me save hoti hain (chmod 600, gitignored).
   Purana `.nexus/keys.json` auto-migrate. Env keys + file keys dono chalti hain.
2. **Slash autocomplete** — bas `/` type karo, saare commands hint ke saath
   dikhte hain; `/skill <tab>` skill ids, `/agent <tab>` names, `/mode <tab>`
   modes complete hote hain. Arrow-keys se history (persist bhi hoti hai).
   (`pip install prompt_toolkit` — setup.sh already karta hai; na ho to
   simple input fallback.)

## What's new in v1.2 (aapke live Termux feedback se)

1. **Calculator jhooth band** — `8282+282282` jaise sawal ab LLM ke bina locally
   solve hote hain (pehle router ne 601144 bola tha — galat). `CALC` phase = exact.
2. **Device sawal ab sach me check hote hain** — battery/storage/wifi/network poocho
   to agent `system_info` + shell se probe karta hai (`/sys/class/power_supply`,
   Termux `termux-battery-status`). "Access nahi hai" wala jhooth gone.
3. **Project isolation** — har build goal apna folder banata hai
   (`workspace/projects/<slug>/`). Naye projects kabhi mix nahi honge. `/projects` se list.
4. **Live processing indicator** — har LLM call pe `thinking · worker · 14s` spinner+timer
   chalta hai. Screen ab kabhi "ruka hua" nahi lagta.
5. **Cocoa theme (default)** 🤎 — agent ka kaam dim brown me, user message/RESULT/PLAN
   apne original colors me. (`-t cyber` se purana mil sakta hai)
6. **Frontend skill ab enforce hota hai** — 12 quality-gates (`:root` tokens, `@media`,
   `:focus-visible`, 44px tap targets, 120+ line CSS...) critic grep karke check karta hai;
   thin/lazy UI fail hoti hai.

## Testing & real-world audit

### Offline unit tests (101 tests, no API needed)
```bash
python3 -m pytest tests/test_core.py -q
```

### Real interactive TUI test (pty session — jaise user terminal me chalaye)
```bash
python3 tests/test_tui_session.py          # full: banner, chat, build task, approval y/n + deny
python3 tests/test_tui_session.py fast     # sirf smoke commands
```

### Live-audited safety (adversarial pty sessions se proven)
Real TUI runs me agent ne ye evasions try kiye — sab deterministic harness rules se band hain:

| Evasion (live me pakda gaya) | Defence (model pe trust nahi) |
|---|---|
| Router ne "Deleted!" jhooth bola (tools hi nahi the) | `router_guard()` — action requests kabhi direct-answer nahi |
| Files `workspace/workspace/` me gayi (double path) | `_resolve()` dedup — relative + absolute dono |
| Deny ke baad `move_path` se `.deleted` rename karke circumvent | **denied-path freeze** — denied targets par koi tool nahi chalta |
| `run_python` me `os.system("rm")`, `shred`, `find -delete`, `.trash` move | **deletion choke-point** — run_shell/run_python me deletion hard-block; sirf `delete_path` (human approval) se delete hota hai |
| Purane session ki memory se galat file plan (`todos.json`!) | Planning ko sirf preferences milte hain, task summaries nahi |

### Ek chakkar me sab dekho
```bash
python3 nexus.py
nexus ❯ /keys
nexus ❯ namaste, tum kaun ho?                  # router direct — supervisor bypass
nexus ❯ /auto Create squares.py that prints 1-10 squares, run it, save output to squares.txt
nexus ❯ delete squares.txt permanently          # approval panel — 'n' dalo, file bachegi
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
`nexus/providers/registry.py`.

---

## Project structure

```
nexus-agent/
├── nexus.py                    launcher
├── setup.sh                    one-command setup (deps + purge + self-test)
├── config/config.yaml          all configuration
├── skills/                     markdown playbooks
├── workspace/                  where the agent builds things
├── tests/
│   ├── test_core.py            72 offline unit tests
│   └── test_live.py            live API integration tests
└── nexus/
    ├── cli/          app.py (REPL), ui.py (rich terminal UI)
    ├── core/         config.py, context.py, jsonutil.py
    ├── providers/    keyring.py (failover), mistral.py, openai_compat.py, registry.py
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
