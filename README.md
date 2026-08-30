<p align="center">
  <img src="https://img.shields.io/badge/DeepSeek-Native-4D6BFE?style=flat" alt="DeepSeek-Native"/>
  <img src="https://img.shields.io/badge/Termux-Ready-2D9CDB?style=flat" alt="Termux-Ready"/>
  <img src="https://img.shields.io/badge/tests-171%20passing-27AE60?style=flat" alt="tests"/>
  <img src="https://img.shields.io/badge/license-MIT-8E44AD?style=flat" alt="MIT"/>
</p>

# DeepSeek-Agent

**A fully autonomous, DeepSeek-native multi-agent CLI that runs on your phone (Termux) or desktop.** Log in with your DeepSeek account once — no API keys, no tokens to paste. It plans, codes, researches, builds, tests and hosts real projects end-to-end, and it **never stops on its own**: it keeps working (and re-planning) until the job is actually done and verified.

> Everything runs on **DeepSeek**. The agent talks to `chat.deepseek.com` using the same login as the official app and switches between DeepSeek's native modes — **instant · expert · vision** — with a single `/mode` command.

---

## Highlights

- 🪪 **Login with your DeepSeek account** (email + password). The bearer token is stored on-device with `chmod 600` and **auto-refreshed** when it expires.
- 🧠 **Native DeepSeek modes** — `instant` (fast replies), `expert` (deep reasoning), `vision` (image understanding) — switch anytime with `/mode`.
- 🤖 **Six specialist agents** (router, supervisor, worker, coder, researcher, critic) that collaborate on a task DAG and verify their own work.
- 🛠️ **Real tools** — filesystem, shell, Python, web search, browser, Git, SQLite, PDF/DOCX/PPTX, and a hosted `start_server` (not a fake "run this command yourself").
- 🏗️ **Builds & hosts real projects** — portfolio sites, APIs, scripts — then verifies `HTTP 200` + a content marker before claiming success.
- 📚 **Skills, RAG and memory** — markdown playbooks, keyword search (DeepSeek has no embedding endpoint, so RAG falls back to fast keyword retrieval), and SQLite session memory with `/resume`.
- 🛡️ **Safety that was adversarial-tested** — sandboxing, SSRF guard, deletion choke-points, and `smart / always / never` approval modes.
- 📱 **Termux-first** — pure-Python, no Playwright/Chromium; the login is WAF-aware with a paste-token fallback.

---

## Requirements

- **Python 3.9+** (pure Python; no binary wheels needed)
- **Node.js** — required for DeepSeek's Proof-of-Work (PoW) challenge on first use (`pkg install nodejs` on Termux). The solver downloads a small WASM file once, automatically.
- Internet access to `chat.deepseek.com`.

---

## Install

**One command does everything:**

```bash
git clone https://github.com/Saurabh-gzp/deepseek-agent.git
cd deepseek-agent
bash setup.sh
```

`setup.sh` auto-detects Termux / Linux / macOS and:
1. Installs system packages (`nodejs`, etc.).
2. Installs Python deps (`rich`, `PyYAML`, `requests`, `numpy`, `prompt_toolkit`).
3. Cleans up any older install (removes a pre-existing `nexus`/`deepseek` command/alias).
4. Installs the **`deepseek`** command.
5. Self-tests the core imports and prints the launch command.

> 💡 **Updating later:** `git pull && bash setup.sh --update` re-installs dependencies but **keeps your keys/config**. A plain `bash setup.sh` is a fresh setup.

---

## First run

```bash
deepseek
```

On the very first run a **login wizard opens automatically**. Enter your **DeepSeek** account email + password (the same account you use on chat.deepseek.com). The agent:

- logs in, obtains a bearer token and stores it **on your device only** (`keys/deepseek_token`, `chmod 600`);
- **auto-refreshes** the token via your credentials whenever it expires;
- falls back to **paste-token** mode on Termux when no browser is available (the AWS WAF is handled for you).

No API key. No "sign up at a console". Just your DeepSeek account.

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

### Slash commands

Typing `/` lists every command with hints (enable autocomplete with `DEEPSEEK_FANCY_INPUT=1`).

```
/help                    /status              /keys [add <key>]
/skills [query]          /skill <id>          /tools
/rag                     /index <path>        /forget-index
/memory                  /remember k=v        /sessions   /resume <id>
/plan <goal>             /auto <goal>         /agent <name> <task>
/mode auto|instant|expert|vision            # DeepSeek mode (auto = pick per task)
/mode smart|always|never                    # approval mode
/cd <path>               /verbose            /clear      /exit
```

---

## How it works (architecture)

```
┌──────────────────────────── DEEPSEEK (chat.deepseek.com) ────────────────────────────┐
│   email + password  →  bearer token  →  native modes: instant · expert · vision      │
└──────────────────────────────────────────┬───────────────────────────────────────────┘
                                           │
                          ┌────────────────┴─────────────────┐
                          │           ROUTER (decider)        │
                          │  intent → orchestrate or answer   │
                          └────────────────┬─────────────────┘
                                            │ task goal
                          ┌─────────────────▼─────────────────┐
                          │         SUPERVISOR (planner)       │
                          │  splits goal into a task DAG       │
                          └─────────────────┬─────────────────┘
                  ┌──────────────────────────┼──────────────────────────┐
                  │                          │                          │
           ┌──────▼──────┐            ┌──────▼──────┐            ┌──────▼──────┐
           │   WORKER     │            │    CODER    │            │  RESEARCHER │
           │ data, device │            │  code, web  │            │ live info   │
           └──────┬──────┘            └──────┬──────┘            └──────┬──────┘
                  │                          │                          │
                  └──────────────────────────┴──────────┬───────────────┘
                                                        │
                                          ┌─────────────▼─────────────┐
                                          │    CRITIC (verifier)       │
                                          │  checks acceptance,        │
                                          │  fails → supervisor replans│
                                          └───────────────────────────┘
```

Every specialist runs on **DeepSeek** — expert mode by default, instant for fast data work, vision for images. The model name is a placeholder; DeepSeek's web API ignores it and the mode is chosen in the chat payload.

### The thing that matters most: it does not stop

- It re-plans on failure and **reuses completed work** (keeps the same project folder — it won't create a duplicate next to it).
- It **hosts and verifies** sites itself with `start_server` and checks for an `HTTP 200` + the exact `<title>` marker — it will not hand you a "run this yourself" command.
- The **critic** verifies acceptance criteria; if it fails, the supervisor replans the failed task only.
- Hard time budgets and an honesty rule keep it from fabricating results.

---

## Modes (automatic + manual)

DeepSeek has three native modes, and DeepSeek-Agent **picks the right one
automatically for every task** (`/mode auto` is the default):

| The task looks like… | Mode chosen |
|---|---|
| conversation, chat, quick questions, simple math | **INSTANT** |
| coding, building, research, debugging, complex work | **EXPERT** |
| an image / screenshot / photo | **VISION** |

So `9393383+8383883` runs on **INSTANT** (fast and cheap), while `build me a
todo API` automatically runs on **EXPERT**. You can still pin a mode:

| Command | Effect |
|---|---|
| `/mode auto` | pick instant/expert/vision per task (default) |
| `/mode instant` · `/mode expert` · `/mode vision` | pin that mode |
| `/think on\|off` | toggle the reasoning chain |
| `/search on\|off` | toggle DeepSeek's native web search |

The active mode shows in the banner.

**Conversation memory:** recent turns are threaded into every run, so a follow-up
like `+8383838383` right after an arithmetic result continues the calculation
instead of being treated as a brand-new, ambiguous input.

---

## Configuration

Everything lives in **`config/config.yaml`** and mirrors to environment variables `DEEPSEEK_*`.

```yaml
app:
  name: "DeepSeek-Agent"
  workspace: "./workspace"     # where the agent builds things
  data_dir: "./.deepseek"      # db, vectors, logs, sessions
  theme: "cocoa"

providers:
  default: deepseek
  deepseek:
    enabled: true
    type: deepseek
    mode: expert               # instant | expert | vision
    thinking: true
    search: false
    timeout: 180
```

Optional extra providers (Groq, Ollama, etc.) remain available under `openai_compatible` — all disabled by default because DeepSeek is the engine.

---

## Safety

DeepSeek-Agent is sandboxed and adversarial-tested:

- 🔒 **Sandbox** — reads/writes are confined to the workspace; path escapes are blocked.
- 🌐 **SSRF guard** — `http_request`/`web_fetch` refuse loopback and cloud metadata (`169.254.169.254`).
- 🗑️ **Deletion choke-point** — `rm`, `os.remove`, `find -delete`, `move-to-trash` all route through one approved `delete_path` and never evade a user denial.
- 🎛️ **Approval modes** — `smart` (ask only for destructive actions) · `always` · `never`.
- 🧩 **Denied-path freeze** — once you deny deleting a file, every trick (rename, shell, python) to touch it is blocked too.

---

## Project structure

```
deepseek-agent/
├── deepseek.py                 launcher (command: `deepseek`)
├── setup.sh                    one-command setup
├── config/config.yaml          all configuration
├── skills/                     markdown playbooks (deepseek_agent/skills/loader.py)
├── workspace/                  where the agent builds things
├── tests/
│   ├── test_core.py            171 offline unit tests (no API calls)
│   └── test_live.py            live integration tests against DeepSeek
└── deepseek_agent/
    ├── cli/          app.py (REPL), ui.py (rich terminal UI), completer.py
    ├── core/         config.py, context.py, keymanager.py, jsonutil.py
    ├── providers/    deepseek.py (engine), keyring.py, openai_compat.py, registry.py
    ├── llm/          client.py (roles, rate limiting, fallback)
    ├── agents/       base.py (ReAct loop), specialists.py (6 agents)
    ├── orchestrator/ dag.py (scheduler), engine.py (autonomous loop)
    ├── tools/        base.py, filesystem.py, shell.py, web.py, browser.py, gitops.py
    ├── rag/          engine.py, store.py
    ├── memory/       store.py
    ├── skills/       loader.py
    └── safety/       guard.py, ssrf.py
```

---

## Testing

```bash
python3 -m pytest tests/test_core.py -q    # 171 offline tests, ~7s, no API calls
python3 tests/test_live.py                 # live integration against DeepSeek
```

The offline suite covers KeyRing failover, DAG scheduling, filesystem sandbox, shell safety, SSRF, JSON/critic parsing, approval policy, RAG and routing guards. (There are 7 known, pre-existing failures that are unrelated to the provider and are tracked separately.)

---

## Termux notes

- `termux-wake-lock` before long runs, or Android may kill the process.
- **Settings → Battery → Termux → Unrestricted**.
- `pkg install nodejs` (required for the DeepSeek PoW solver).
- `pkg install python-numpy` rather than `pip install numpy`.
- `Killed` means out of memory: lower `autonomy.max_parallel_agents` to 2.
- No Playwright/Chromium — DeepSeek-Agent is pure Python and WAF-aware, so it works without a browser.

---

## License

MIT
