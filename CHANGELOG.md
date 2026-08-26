# Changelog

## [1.5.0] — capability-aware autonomous agent + live-audit fixes

**Model inventory (live-verified against the API, 2026-08-26):**
- 56 models exist on the org key; 13 chat models tested live — all respond except
  `mistral-large-2512` (hangs >180s on this key — moved to second fallback) and
  `labs-leanstral-1-5-1` / `glm-5-2` (HTTP 403 — not enabled for the org).
- Embeddings verify OK (mistral-embed-2312 dim 1024, codestral-embed dim 1536);
  moderation OK; OCR OK (data-URI PDFs).
- Config rate_limits now MATCH the real organization limits
  (admin.mistral.ai → Limits): embed 1.0 rps (was 4.0 — caused 429s),
  small 0.83, 8b 3.13, 3b 12.5, codestral 2.08, devstral 0.83, large 0.07.

**Capability-aware routing & planning (the big one):**
- Router is now the **ministral-8b-2512 decider**: it classifies the request and
  emits `task_type` (device|web|code|data|general) + `model_hint`; the
  supervisor's plan is steered by that hint instead of planning blind.
- Supervisor plan prompt now carries a **MODEL CAPABILITY TABLE** and assigns
  every task to the agent whose model fits: coder=devstral/codestral (code,
  bug fixes, website/UI), researcher=mistral-small (web/live info),
  worker=ministral-8b/14b (data, summaries, device queries), critic=medium.
- New optional per-task `"model"` pin (validated against a whitelist) — the
  supervisor can force `codestral-2508` for a small code task etc. The UI plan
  table now shows the model that will run each task (frontend = simple plan,
  detail stays in the backend).
- Worker fallback chain now includes ministral-14b-2512 (verified live).

**Token/time-waste fix (live bug: "storage info" burned 251s / 144,732 tok):**
- New `device_info` tool — one-shot, CORRECT device report (storage via
  Termux paths `~/storage/*`, `df -h /data /storage/emulated/0`, battery,
  network, memory). No more guessing `/sdcard/*` on Termux (does not exist)
  and 5 failed `du` runs per query.
- Worker prompt: never run a command blind — use device_info or web-search the
  exact command; unknown paths must not be fired as guesses.
- Plan rules: device/system queries = exactly ONE worker task calling
  device_info; never coder; never command experiments. Live queries →
  researcher, never coder.
- Critic + engine: any task whose tool calls errored can no longer score
  100-pass without justification — verdict is capped at 'partial' (79) unless
  the critic re-verifies the affected data itself. (Live bug: storage task
  with 5 failed `du` runs still scored 100.0.)

**Bugs fixed:**
1. `MemoryStore.resolve_session` — an all-digit session-id prefix (e.g.
   `123456`) was misread as a session NUMBER and returned None
   (`/resume <prefix>` broken for ~3.7% of sessions). Exact-id is now checked
   first and out-of-range numbers fall through to prefix matching.
2. `tests/test_live.py` — asserted artifacts at the workspace root, but
   v1.2+ project isolation puts them in `projects/<slug>/` → 2 false failures.
   Now checks both locations; also reports the real client's token stats.
3. `tests/test_tui_session.py` — SyntaxError on line 24: the "English-only"
   commit merged two lines into `ROOT = ... ART = ...`. Fixed; ROOT is now
   derived from `__file__` instead of being hardcoded.

**Tests:** 133/133 offline (was 132 pass + 1 flaky); live suite 26/26.

# Changelog

## [1.4.2] — "workspace clean" disaster fix + full tool audit

**Root causes fixed (live pty-verified):**
1. `delete_path` now accepts `path`/`src`/`target` aliases — the agent's `src:`
   parameter no longer raises a TypeError after approval.
2. Added `delete_path`/`run_shell`/`move_path` to the worker agent's allowed
   tools — previously the worker had NO way to delete, so it just asked the
   user for a "YES" in plain text.
3. DELETE/clean-only goals no longer create a `projects/<slug>/` folder
   (engine `_apply_project_scope` skips). "workspace clean" no longer
   pollutes the scope.
4. Approval 'a' (always) is now ACTION-level — one 'a' covers the whole
   batch for that action (live proof: 1 prompt → 7 delete_path calls
   proceed silently).
5. Critic retry-exhaustion no longer marks a task 'done': hard-verify
   fail/unavailable → honestly FAILED. Borderline acceptance only at
   score ≥ 60 + verdict 'partial'.
6. Worker/supervisor prompts: deletions must use delete_path (the only
   path), planning 'confirm with user' tasks for deletions is forbidden.
   The critic is now project-scope aware — root-location false-conflict
   retries eliminated (build: 55s → 21s).
7. Tool errors are visible in the UI (`↳ reason` under `✕`) — no more
   silent failures.

**Verified:** tool suite 22/22 by direct execution (fs/shell/python/web/
skills/memory/RAG + BLOCKED rm/python deletes), live pty run of
"clean the workspace, delete everything" → 1 approval, 7 deletes,
critic 100.0 pass, workspace EMPTY, no manual `rm -rf`.
Build-goal regression: `projects/<slug>/` isolation intact. Tests: 128 pass.
Setup: one-command `setup.sh` (deps + old-key purge + self-test + launch
instructions); `--update` mode keeps keys. Entire codebase now English-only.
