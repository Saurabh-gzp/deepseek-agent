---
name: Task Management
description: Break a multi-deliverable goal into a DAG, track done/blocked, and refuse to skip a phase. Use for multi-step projects, ops-lab, "do all of this", task board, kanban, project plan with several artifacts.
tags: [plan, dag, kanban, tasks, phases]
version: 1.0
agents: ["supervisor", "coder", "worker"]
---

# Skill: Task Management

## When to use
Goal lists 3+ deliverables (site + db + pdf + host…).

## Procedure
1. Write `projects/<slug>/TASKS.md` as a checklist **before** building:
   `- [ ] research` / `- [ ] schema` / `- [ ] app` / `- [ ] slides` / `- [ ] host`
2. After each artifact, flip the box to `[x]` via `edit_file`.
3. Do not mark host done without `start_server` evidence.
4. Final answer lists TASKS.md status honestly.

## Anti-patterns
❌ Saying all phases done while TASKS.md still has empty boxes
❌ Skipping host because "user can open the html"
---
