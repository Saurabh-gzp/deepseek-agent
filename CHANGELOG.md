# Changelog

All notable changes to **DeepSeek-Agent**.

> This project is now **DeepSeek-native** (runs entirely on `chat.deepseek.com`,
> email+password login, native instant/expert/vision modes). Earlier Mistral-era
> history was folded into the v2.0.0 rebrand below.

---

## [2.0.0] — DeepSeek-native rebrand, Mistral removed, E2E hardening

**Rebrand / structure**
- Package is `deepseek_agent`, launcher `deepseek.py`, installed command `deepseek`,
  data dir `.deepseek`, env vars `DEEPSEEK_*`. Agent name is **DeepSeek-Agent**.
- **Mistral provider removed entirely** — code, config, README, docs, tests. The
  registry is DeepSeek-only (`deepseek` + optional `openai_compatible`).
- New complete README; `TERMUX.md`, `DeepSeek-AGENT.md`, `ARCHITECTURE.md` updated.

**Provider (DeepSeek via `requests` + Android UA)**
- Login uses the official Android app user-agent (`DeepSeek/2.0.2 (Android; API)`)
  so the AWS WAF lets it through (the desktop `Mozilla/5.0` UA got challenged).
- Email+password → bearer token, cached `chmod 600`, **auto-refreshed** on expiry.
- Proof-of-Work solved with a bundled Node.js + WASM solver (auto-downloaded once).
- Native modes **instant · expert · vision**; session id parsed from `data.biz_data.id`.

**Automatic mode selection (new)**
- `/mode auto` (default) picks the DeepSeek mode per task:
  **vision** → image/screenshot/photo; **expert** → coding/build/research/debug;
  **instant** → conversation, chat, quick questions, simple math.
- `/mode instant|expert|vision` pins a mode; `/mode auto` re-enables auto-picking.
- `/think on|off` and `/search on|off` added to the `/`-completion menu and hint.

**Memory / conversation continuity (fix)**
- Recent turns are threaded into each focused run as context, so a follow-up like
  `+8383838383` after an arithmetic result continues correctly instead of being
  treated as a brand-new, ambiguous task.

**E2E fixes (found by running real tasks)**
- `start_server` port collision: `SO_REUSEADDR` on the availability probe so a
  stopped server's `TIME_WAIT` socket no longer blocks an immediate restart (a live
  active listener is still refused).
- DIY handoff stripped from final answers whenever hosting is required, even if a
  server was verified earlier.
- Solo agent "COMPLETION RULE": must execute + verify every requested step, not stop
  after creating files.
- Approval prompt: EOF in non-interactive/one-shot runs denies safely instead of
  crashing with a traceback.

**Tests:** 171 offline unit tests pass; 7 known pre-existing failures unrelated to
the provider (tracked separately).

---

## [1.x] — legacy autonomy engine (pre-rebrand)

The full autonomy engine (router/supervisor/worker/coder/researcher/critic,
tools, RAG, memory, skills, safety, approvals, hosting verification, live TUI
audits) was developed through 1.x. With v2.0.0 the backend is DeepSeek-native and
the Mistral-era specifics no longer apply.
