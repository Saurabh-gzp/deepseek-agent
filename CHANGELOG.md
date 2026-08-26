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
