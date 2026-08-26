---
name: Make Plan
description: Break any goal into an executable, verifiable task DAG with owners, dependencies and acceptance criteria. Use at the START of any multi-step or ambiguous request, before writing code or doing research.
tags: [planning, decomposition, dag, strategy]
version: 1.0
agents: ["*"]
---

# Skill: Make a Plan

## When to use
- The goal needs more than 2 actions
- Requirements are vague ("build me an app")
- Multiple agents/tools will be involved
- A previous attempt failed and needs re-planning

## Procedure

### 1. Clarify the goal (30 seconds, no tools)
Write one sentence: **"Success = ___ exists and ___ works."**
If you cannot write that sentence, the goal is under-specified — pick the most
reasonable interpretation, state the assumption explicitly, and continue.
Never stall an autonomous run waiting for clarification unless it is truly blocking
(missing credential, destructive ambiguity).

### 2. Inventory what already exists
- `list_dir` the workspace
- `search_knowledge` for prior work on the same topic
- `recall` user preferences
Do not plan work that is already done.

### 3. Decompose (2–8 tasks)
Good task = one owner, one deliverable, one checkable outcome.

| Bad | Good |
|---|---|
| "Build the app" | "Create `app/api.py` with 4 REST endpoints returning JSON" |
| "Research it" | "Find 3 sources on X, output `research/x.md` with citations" |
| "Test" | "Run `pytest -q`; all tests pass with exit code 0" |

Split by **deliverable**, not by verb. Merge tasks that touch the same file.

### 4. Assign owners
| Agent | Give it |
|---|---|
| `researcher` | web search, doc reading, source comparison, citations |
| `worker` | summarising, formatting, data shaping, glue work |
| `coder` | writing/fixing/running code, shell, tests |
| `critic` | final verification only |

### 5. Wire dependencies
- Two tasks writing the same file → `depends_on`, never parallel
- Research → implementation → test is a chain
- Independent files → parallel (`parallel_safe: true`)

### 6. Write acceptance criteria
Every task ends with something a machine can check:
- `file X exists and contains function Y`
- `command Z exits 0`
- `output lists >= 3 sources with URLs`
Vague criteria ("looks good") = the critic cannot verify = wasted retries.

### 7. Order and budget
Front-load risk: do the task most likely to fail FIRST, so replanning is cheap.
Keep the plan under 8 tasks; if bigger, make the last task "plan phase 2".

## Output format
```json
{
  "goal_restated": "...",
  "strategy": "...",
  "tasks": [
    {"id":"t1","title":"...","description":"...","agent":"coder",
     "depends_on":[],"skill":"coding/backend_api_design",
     "acceptance":"...","parallel_safe":true}
  ],
  "final_deliverable": "..."
}
```

## Anti-patterns
- ❌ 15 micro-tasks (orchestration overhead > work)
- ❌ Tasks with no file/command output (nothing to verify)
- ❌ Circular dependencies
- ❌ "Research best practices" as a task when you already know the answer
- ❌ Planning past the horizon — replan after phase 1 instead of guessing phase 3
