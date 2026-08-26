---
name: Deep Research
description: Run rigorous multi-source web research — query strategy, source triangulation, citation discipline, contradiction handling and report writing. Use for any "research X", "compare Y vs Z", "find out about", market/tech analysis or fact-gathering task.
tags: [research, web, sources, citations, analysis, report]
version: 1.0
agents: ["researcher", "worker", "supervisor"]
---

# Skill: Deep Research

## Process
```
DEFINE questions → SEARCH broad → TRIANGULATE (3+ sources) → EXTRACT with citations
→ RESOLVE contradictions → SYNTHESISE → STATE what is still unknown
```

## 1. Define before searching
Write 3–6 specific sub-questions. "Research electric cars" is not a question;
"What is the 2026 price range of EVs in India, and how has it moved since 2024?" is.
Note what a *good* answer looks like: numbers, dates, named entities, tradeoffs.

## 2. Query craft
| Goal | Query pattern |
|---|---|
| Current facts | `topic 2026` / `topic latest` |
| Official truth | `site:docs.x.com topic` / `topic official documentation` |
| Comparison | `X vs Y benchmark` / `X alternatives comparison` |
| Numbers | `topic statistics report pdf` |
| Practical | `topic tutorial site:github.com` / `topic "how we"` |
| Criticism | `topic problems limitations criticism` ← always run this one |

Run 3–5 varied queries, not one. Vary vocabulary (technical + colloquial).

## 3. Source quality ladder
```
1. Primary        official docs, spec, filings, the actual paper, the source code
2. Reputable      major publications, established orgs, peer-reviewed
3. Practitioner   detailed blog posts with data/repro, conference talks
4. Aggregated     Wikipedia (use for orientation + follow its refs)
5. Weak           forums, marketing pages, undated posts, SEO listicles
```
- **Check the date on everything.** In fast-moving tech, >18 months old is suspect; say so.
- Prefer the source over the article about the source. Follow citations upstream.
- A vendor's page about its own product is marketing, not evidence — label it.

## 4. Triangulate
Any load-bearing claim needs **2–3 independent sources**. Independent means not
all quoting the same press release.
```
Claim: "Model X scores 72% on SWE-bench"
  [1] vendor blog        → 72%  (vendor, may be best-case)
  [2] independent eval   → 65%  (different harness)
  [3] leaderboard        → 68%  (dated 2026-03)
→ Report: "65–72% depending on harness; vendor-reported figure is the highest [1][2][3]."
```

## 5. Citation discipline (non-negotiable)
- Every fact, number, date, quote → inline `[n]` marker.
- Sources list at the end: `[n] Title — URL (accessed YYYY-MM-DD)`.
- **Never invent a URL, a statistic, a date or a quote.** If you did not fetch it, you do not have it.
- If a page failed to load, say "could not verify" rather than guessing its contents.
- Distinguish: *fact* (sourced) / *estimate* (derived, show the maths) / *opinion* (yours, labelled).

## 6. Handle contradictions explicitly
Do not average conflicting numbers silently. Report:
> Sources disagree: [2] reports 40M users (Jan 2026), [5] reports 55M (Mar 2026).
> The gap is likely definitional (MAU vs registered). Using [5] as more recent.

## 7. Report template
```markdown
# <Question>
*Researched YYYY-MM-DD · N sources*

## Summary
3–5 bullets. The actual answer, up front, with numbers.

## Findings
### <Sub-question 1>
Evidence with [n] markers. Tables for comparisons.

## Contradictions & uncertainty
What sources disagree on; what could not be verified.

## Implications / Recommendation
So what? Concrete, tied to the user's context.

## Sources
[1] Title — URL (accessed 2026-08-25) — primary/reputable/practitioner
```

## Efficiency rules for agents
- Search → skim the snippets → fetch only the 2–4 most promising pages. Fetching 10 pages
  wastes the context budget and rarely changes the answer.
- Extract to notes as you go (`write_file research/notes.md`); do not hold everything in context.
- Index long findings with `index_knowledge` so later tasks can retrieve them.
- Stop when new sources stop adding new information (saturation), not when you run out of time.

## Anti-patterns
❌ One source = a conclusion · ❌ undated claims presented as current ·
❌ fabricated URLs or "approximately" numbers with no source ·
❌ summarising the search snippets without opening anything ·
❌ burying the answer under process narration · ❌ ignoring the critical/negative sources
