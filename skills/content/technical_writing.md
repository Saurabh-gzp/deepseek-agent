---
name: Technical Writing
description: Write clear READMEs, documentation, guides, reports and explanations that people actually use. Use for any documentation, README, tutorial, report or written deliverable task.
tags: [writing, documentation, readme, report, communication]
version: 1.0
agents: ["worker", "researcher", "coder"]
---

# Skill: Technical Writing

## Core rules
1. **Answer first.** Conclusion in the first two lines, detail after. Nobody reads to the end.
2. **Show, don't describe.** A runnable command beats a paragraph about the command.
3. **Second person, active voice, present tense.** "Run `x`" not "The user should run `x`".
4. **One idea per paragraph, max 4 sentences.**
5. **Every claim is testable** — if the reader copies it, it works.

## README template (the only one you need)
````markdown
# Project Name
One sentence: what it does and who it is for.

## Install
```bash
git clone <repo> && cd project
pip install -r requirements.txt
```

## Quick start
```bash
python main.py --input data.csv
# → wrote report.md (42 rows)
```

## Configuration
| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | yes | – | Provider key |
| `TIMEOUT` | no | 30 | Seconds |

## Usage
Two or three real examples, each with its actual output.

## How it works
5–10 lines + a small diagram. Only if it helps someone modify it.

## Troubleshooting
| Error | Fix |
|---|---|

## License
````

## Structure patterns
- **Tutorial** — learning-oriented, one happy path, no options, ends with a working thing.
- **How-to** — task-oriented, assumes context, lists alternatives.
- **Reference** — complete, alphabetical/structural, no narrative.
- **Explanation** — understanding-oriented, background and tradeoffs.
Do not mix these in one document; readers arrive with one need.

## Formatting for scanability
- Headings every 5–8 lines of prose.
- Tables for anything with 2+ dimensions.
- Numbered lists for sequences, bullets for sets.
- Bold the **decision word**, not whole sentences.
- Code blocks always tagged with a language.
- Keep lines under ~100 chars in source markdown.

## Word discipline
| Cut | Use |
|---|---|
| "in order to" | "to" |
| "utilise" | "use" |
| "it is important to note that" | (delete) |
| "very fast" | "12ms" |
| "simply/just/easily" | (delete — it insults the stuck reader) |
| "should work" | test it, then say "works" |

Define an acronym on first use. Never say "obviously" or "as everyone knows".

## Reports (research/analysis)
```markdown
# Title
*Date · scope · sources*

## Summary          ← 3–5 bullets, the actual findings with numbers
## Findings         ← evidence with [n] citations, tables
## Limitations      ← what you could not verify
## Recommendation   ← concrete next action
## Sources          ← [n] Title — URL (accessed date)
```

## Error messages & CLI copy
```
✕ Cannot read config.yaml: file not found
  → Create it:  cp config.example.yaml config.yaml
```
Pattern: **what failed + why + the exact fix command.** Never "Error occurred".

## Before you ship
```
□ Every command in the doc was actually run and its output pasted is real
□ No placeholder like <YOUR_PATH_HERE> left unexplained
□ Install steps work on a clean machine
□ Headings form a sensible outline on their own
□ Spell-check; consistent product name capitalisation
□ Under 500 lines — split if longer
```

## Anti-patterns
❌ Walls of prose · ❌ documenting the code line by line · ❌ untested commands ·
❌ "TODO: write this section" shipped · ❌ marketing tone in technical docs ·
❌ screenshots for text that could be copy-pasted
