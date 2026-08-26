---
name: Office Documents
description: Create real PowerPoint PPTX, PDF, and Word DOCX files. Use when the user asks for a presentation, slides, deck, briefing, invoice PDF, report PDF, Word doc, proposal, or printable handout. Triggers: ppt, pptx, slides, powerpoint, pdf, invoice, docx, word document.
tags: [pptx, pdf, docx, slides, presentation, invoice, report]
version: 1.1
agents: ["coder", "worker"]
---

# Skill: Office Documents

## When to use
User wants a **real file** they can open in PowerPoint / Acrobat / Word — not markdown pretending to be a deck.

Do NOT use for "write a README" or HTML pages.

## Procedure
1. `list_dir` the project folder. Write files under `projects/<slug>/`.
2. Call the dedicated tool — do **not** invent binary bytes with `write_file`.
   - Slides → `make_pptx(path=..., title=..., slides=...)`
   - PDF report/invoice → `make_pdf(path=..., title=..., body=...)`
   - Word → `make_docx(path=..., title=..., body=...)`
3. `slides` format for PPTX: blocks separated by a line containing only `---`. Each block starts with `## Slide title` then bullet lines.
4. After the tool returns, `find_files` to prove the path exists. Report **path + byte size** from the tool output.
5. Never claim "I created a PPT" if `make_pptx` was not called.

## Checklist
- [ ] Real `.pptx` / `.pdf` / `.docx` extension
- [ ] Tool output shows byte size > 1000
- [ ] Title matches the user's requested title
- [ ] 4–8 slides for a briefing (not 1 empty slide)

## Anti-patterns
❌ Writing `deck.md` and calling it a presentation
❌ Base64-faking a pptx
❌ `python -m http.server` instead of delivering the file
---
