# Nexus Skills

Skills are markdown playbooks that give the agent expert procedures on demand.
They use **3-level progressive disclosure** so having 50 skills costs almost no context:

| Level | Content | Loaded |
|---|---|---|
| 1 | YAML frontmatter (`name`, `description`) | always, in the system prompt (~60 tokens each) |
| 2 | Markdown body | when the agent calls `load_skill("<id>")` |
| 3 | Linked reference files / scripts | only when the agent reads them |

## Directory layout

```
skills/
├── plan/
│   └── make_plan.md
├── web_development/
│   ├── frontend_ui_ux_design.md
│   └── backend_api_development.md
├── automation/
│   ├── webautomation/
│   │   └── web_automation.md
│   └── make_automation_script/
│       └── web_automation.md
├── coding/
│   ├── python_project_structure.md
│   └── debugging_and_testing.md
├── research/
│   └── deep_research.md
├── data/
│   └── data_analysis.md
├── devops/
│   └── termux_environment.md
└── content/
    └── technical_writing.md
```

The **skill id** is the path without `.md`, e.g.
`automation/webautomation/web_automation`.

## Writing a new skill

Create `skills/<category>/<name>.md`:

```markdown
---
name: Human Readable Name
description: What it does AND when to use it. This is the only thing the agent
  sees before deciding to load the skill, so make the triggers explicit.
tags: [keyword, keyword]
version: 1.0
agents: ["coder", "worker"]     # or ["*"] for everyone
---

# Skill: Name

## When to use
## Procedure (numbered, concrete)
## Code/templates to copy
## Checklist / definition of done
## Anti-patterns
```

### Rules that make skills work
- **The `description` is the trigger.** Include the words a user would actually say.
- Keep the body under ~500 lines. Deeper material goes in a `references/` folder next to it.
- Write **procedures and templates**, not essays. Copy-pasteable beats explanatory.
- End with a checklist and an anti-patterns list — these prevent the most failures.
- Restrict `agents:` when a skill is only meaningful for one role.

### Reference files (level 3)
```
skills/coding/
├── python_project_structure.md
└── references/
    └── packaging_deep_dive.md      ← agent reads with read_file when needed
```

## Managing skills from the CLI
```
/skills              list all
/skills api          search
/skill coding/debugging_and_testing    print the full playbook
```
Skills are re-scanned on every `/skills` call — edit a file and it is live immediately.
