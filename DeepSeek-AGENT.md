# DeepSeek-Agent 🚀

This repository has been rebranded and rewired to run entirely on **DeepSeek** —
the same endpoints as the official DeepSeek web app (`chat.deepseek.com`).

It keeps the full autonomy engine (tools, RAG, memory, skills, safety, approvals)
but swaps the LLM backend from Mistral API keys to a **DeepSeek account login**.

---

## What changed

| Area | Before (Nexus) | Now (DeepSeek-Agent) |
|---|---|---|
| Backend | Mistral API keys | **DeepSeek account (email + password)** |
| Login | paste API key | first-run wizard: email + password → token |
| Token lifecycle | static keys | **auto-refresh / regenerate on expiry** (uses saved credentials) |
| Model roles | mistral-* models | DeepSeek native modes: **instant · expert · vision** |
| Tool calling | native function-calling | **text-based tool calling** (DeepSeek has no function API) |
| Embeddings | mistral-embed | none → RAG falls back to **keyword search** |
| Moderation | mistral-moderation | none (harness rules still enforce safety) |

---

## How login works (the part that matters)

DeepSeek's web app is behind **AWS WAF**, so a plain HTTP login is usually
challenged. DeepSeek-Agent tries, in order:

1. **Direct HTTP login** — works when WAF is not challenging your IP.
2. **Headless browser login** (Playwright + Chromium) — passes the WAF JS
   challenge automatically. Auto-detected only when playwright is installed.
3. **Paste a token** — if both of the above fail, the wizard asks you to paste
   a token from your browser (always works).

The token is stored **only on your device** in `keys/deepseek_token`
(chmod 600). Your email + password are stored in `keys/deepseek_account.json`
(chmod 600). Both are gitignored.

When a token **expires or returns INVALID_TOKEN/401**, the provider
**automatically re-logs-in with your saved credentials** to get a fresh token —
so runs keep going without you.

> **Termux note:** Chromium/Playwright do not run on Termux. On Android you'll
> typically use the "paste a token" path (get it from chat.deepseek.com in your
> browser), or run the browser login on a PC once and copy the saved token.

### First run

```bash
bash setup.sh        # installs deps + nodejs (DeepSeek PoW solver)
nexus                # -> asks for your DeepSeek email + password
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

`DeepSeek-Agent` runs a **focused single strong agent** (the `solo` role) that
has every tool: filesystem, shell, python, web, skills, RAG, memory, office,
dbms, git. Any non-`/` input is treated as a goal:

```bash
nexus "build a todo API in the workspace and test it"
nexus -m never "fix the bug in src/app.py"     # full autonomy, no confirmations
```

Because DeepSeek has no function-calling API, tool use is **text-based**: the
model emits `TOOL_CALL: {"name":..., "arguments":{...}}` (or the XML equivalent)
and the harness executes it safely. Supports reasoning, verification, retries,
and a final answer.

---

## Security

- Credentials/token: `keys/`, `chmod 600`, gitignored.
- File writes confined to the workspace sandbox.
- Shell guard blocks dangerous patterns; deletion needs human approval.
- Since DeepSeek exposes no moderation/embedding endpoints, RAG uses keyword
  search and harness-level rules enforce safety.
