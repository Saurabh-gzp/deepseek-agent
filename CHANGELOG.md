
## [1.4.2] — "workspace clean" disaster fix + full tool verification

**Root causes fixed (live pty-verified):**
1. `delete_path` ab `path`/`src`/`target` aliases leta hai — agent ka `src:` param
   approval ke baad bhi TypeError nahi karta.
2. Worker agent ke allowed_tools me `delete_path`/`run_shell`/`move_path` add —
   pehle worker ke paas delete ka KOI rasta nahi tha, wo text me "user se YES"
   maangta reh jata tha.
3. DELETE/clean-only goals pe `projects/<slug>/` folder NAHI banta (engine
   `_apply_project_scope` skip). "workspace clean kr" ab scope-pollute nahi karta.
4. Approval 'a' (always) ab ACTION-level hai — ek 'a' = us action ka poora batch
   (live proof: 1 prompt → 7 delete_path silently proceed).
5. Critic retry-exhaustion pe task 'done' NAHI: hard-verify fail/unavailable →
   FAILED honestly. Borderline-accept sirf score ≥ 60 + verdict 'partial'.
6. Worker/supervisor prompts: deletions ke liye delete_path hi ek rasta hai,
   'confirm with user' tasks plan karna mana. Critic ab project-scope aware —
   root-location false-conflict ke retries khatam (build: 55s → 21s).
7. Tool errors UI me dikhte hain (✕ ke neeche ↳ reason) — silent failures nahi.

**Verified:** tool-suite 22/22 direct-run (fs/shell/python/web/skills/memory/RAG
+ rm/python-delete BLOCK), live pty "workspace clean kr sb kuch delete" →
1 approval, 7 deletes, critic 100.0 pass, workspace EMPTY, no manual rm -rf.
Build-goal regression: projects/<slug>/ isolation intact. Tests: 128 pass.
