# DeepSeek-Agent 🚀

This repository is **DeepSeek-native**: it talks to the same endpoints as the
official DeepSeek web app (`chat.deepseek.com`), driven by a **DeepSeek account
login** — no third-party model, no API keys. It keeps the full autonomy engine
(tools, RAG, memory, skills, safety, approvals) on top of DeepSeek.

---

## What changed

| Area | Now (DeepSeek-Agent) |
|---|---|
| Backend | **DeepSeek** (`chat.deepseek.com`) |
| Login | first-run wizard: DeepSeek email + password → token |
| Token lifecycle | **auto-refresh / regenerate on expiry** (uses saved credentials) |
| Model roles | DeepSeek native modes: **instant · expert · vision** |
| Tool calling | **text-based tool calling** (DeepSeek has no function API) |
| Embeddings | none → RAG falls back to **keyword search** |
| Moderation | none (harness rules still enforce safety) |

---

## How login works (the part that matters)

DeepSeek's web app is behind **AWS WAF**, which challenges desktop-browser
user-agents. DeepSeek-Agent logs in with the **same request profile as the
official Android app** (`requests` + the `DeepSeek/2.0.2 (Android; API)`
user-agent), which the WAF lets through cleanly. The flow:

1. **Direct login** — `POST /api/v0/users/login` with email + password returns a
   real bearer token (HTTP 200). No browser needed.
2. **Proof-of-Work** — the completion endpoint requires a PoW response; the
   agent solves it with a small bundled Node.js solver (WASM auto-downloaded
   once). `nodejs` is required.
3. **Paste a token** — if login is ever blocked, the wizard falls back to pasting
   a token copied from chat.deepseek.com in your browser.

The token is stored **only on your device** in `keys/deepseek_token`
(`chmod 600`); email + password in `keys/deepseek_account.json` (`chmod 600`).
Both are gitignored.

When a token **expires or returns INVALID_TOKEN/401**, the provider
**automatically re-logs-in with your saved credentials** to get a fresh token —
so long runs keep going without you.

> **Termux note:** no browser, no Playwright, no Chromium needed. This works
> entirely with Python `requests` + Node.js, so it runs great on Termux.

### First run

```bash
bash setup.sh        # installs deps + nodejs (DeepSeek PoW solver)
deepseek             # -> asks for your DeepSeek email + password
```

### Change account anytime

```
/login              # re-enter email + password
```

---

## Modes (manual switch)

DeepSeek's native app modes, switched at runtime:

| Command | Mode | Notes |
|---|---|---|
| `/mode instant` | **INSTANT** | fast, supports web-search + files |
| `/mode expert`  | **EXPERT** | deep reasoning (thinking chain) |
| `/mode vision`  | **VISION** | image/document understanding |

Plus:
- `/think on|off`  — toggle the reasoning chain
- `/search on|off` — toggle DeepSeek's native web search (blocked in expert/vision)

The current mode is shown in the banner.

---

## Autonomy

`DeepSeek-Agent` runs a **multi-agent system**: a router decides, a supervisor
plans a task DAG, worker/coder/researcher agents execute with real tools, and a
critic verifies. Any non-`/` input is treated as a goal:

```bash
deepseek "build a todo API in the workspace and test it"
deepseek -m never "fix the bug in src/app.py"     # full autonomy, no confirmations
```

Because DeepSeek has no function-calling API, tool use is **text-based**: the
model emits `TOOL_CALL: {"name":..., "arguments":{...}}` and the harness
executes it safely. Supports reasoning, verification, retries, and a final
answer.

---

## Security

- Credentials/token: `keys/`, `chmod 600`, gitignored.
- File writes confined to the workspace sandbox.
- Shell guard blocks dangerous patterns; deletion needs human approval.
- Since DeepSeek exposes no moderation/embedding endpoints, RAG uses keyword
  search and harness-level rules enforce safety.
