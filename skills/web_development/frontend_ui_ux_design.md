---
name: Frontend UI/UX Design
description: Build modern, responsive, accessible web UIs — layout systems, design tokens, component patterns, dark mode, animation and performance. Use when creating or improving any HTML/CSS/JS/React frontend, landing page, dashboard or web app interface.
tags: [frontend, ui, ux, css, html, react, responsive, accessibility, design]
version: 1.0
agents: ["coder", "worker", "supervisor"]
---

# Skill: Frontend UI/UX Design

## Non-negotiables
1. **Mobile-first** — design 360px first, enhance upward.
2. **One design system** — tokens defined once, used everywhere.
3. **Semantic HTML** — `<header> <nav> <main> <section> <button>`, not div soup.
4. **Accessible** — keyboard reachable, 4.5:1 contrast, focus visible, alt text.
5. **No layout shift** — reserve space for images/async content.

## Design tokens (always start here)
```css
:root{
  /* colour: 1 brand + neutrals + 3 semantic */
  --bg:#0b0f14; --surface:#141a22; --surface-2:#1c242e;
  --text:#e8eef6; --text-dim:#94a3b8; --border:#243040;
  --brand:#00d4ff; --brand-ink:#001318;
  --ok:#22c55e; --warn:#f59e0b; --err:#ef4444;

  /* type scale (1.25 ratio) */
  --fs-xs:.75rem; --fs-sm:.875rem; --fs-md:1rem;
  --fs-lg:1.25rem; --fs-xl:1.563rem; --fs-2xl:1.953rem; --fs-3xl:2.441rem;

  /* 4px spacing scale */
  --s1:.25rem; --s2:.5rem; --s3:.75rem; --s4:1rem;
  --s6:1.5rem; --s8:2rem; --s12:3rem; --s16:4rem;

  --radius:12px; --radius-sm:8px;
  --shadow:0 4px 24px rgba(0,0,0,.35);
  --ease:cubic-bezier(.4,0,.2,1);
}
@media (prefers-color-scheme: light){
  :root{ --bg:#ffffff; --surface:#f8fafc; --text:#0f172a; --text-dim:#475569; --border:#e2e8f0; }
}
```

## Layout recipes
```css
/* Auto-responsive grid — no media query needed */
.grid{ display:grid; gap:var(--s4);
       grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr)); }

/* Page shell */
.shell{ display:grid; grid-template-columns:240px 1fr; min-height:100dvh; }
@media (max-width:768px){ .shell{ grid-template-columns:1fr; } }

/* Centered content with breathing room */
.container{ width:min(1200px,100% - 2rem); margin-inline:auto; }

/* Sticky footer */
body{ min-height:100dvh; display:flex; flex-direction:column; }
main{ flex:1; }
```

## Component checklist
Every interactive component needs **5 states**: default, hover, focus-visible, active, disabled.
Plus for async: loading (skeleton, not spinner-only), empty, error.

```css
.btn{
  display:inline-flex; align-items:center; gap:var(--s2);
  padding:var(--s3) var(--s6); border-radius:var(--radius-sm);
  background:var(--brand); color:var(--brand-ink); font-weight:600;
  border:0; cursor:pointer; transition:transform .15s var(--ease), filter .15s var(--ease);
  min-height:44px; /* touch target */
}
.btn:hover{ filter:brightness(1.1) }
.btn:focus-visible{ outline:2px solid var(--brand); outline-offset:2px }
.btn:active{ transform:translateY(1px) }
.btn:disabled{ opacity:.5; cursor:not-allowed }
```

## UX rules that matter
- **Feedback under 100ms** for every click (optimistic UI or instant spinner).
- **Skeletons beat spinners** for content that has known shape.
- **Errors are actionable**: what failed + what to do, never "Error 500".
- **Forms**: label above input, inline validation on blur (not on keypress), one column.
- **Empty states** get an icon + one line + a primary action.
- **Destructive actions** need confirmation with the object name typed or a clear undo.
- **Max 7 nav items**; everything else in a menu.

## Animation
Only animate `transform` and `opacity`. Durations: 150ms (micro) / 250ms (panel) / 400ms (page).
```css
@media (prefers-reduced-motion: reduce){ *{ animation:none!important; transition:none!important } }
```

## Performance budget
- LCP < 2.5s, CLS < 0.1, INP < 200ms
- Inline critical CSS, defer the rest
- `loading="lazy"` + explicit `width`/`height` on images; prefer WebP/AVIF
- System font stack unless brand demands otherwise:
  `font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif`
- No framework for a static page; no jQuery ever

## Accessibility quick audit
```
□ Tab through the page — order logical, focus always visible
□ Every image: alt (or alt="" if decorative)
□ Every input: <label for> or aria-label
□ Colour is never the only signal (add icon/text)
□ Headings in order h1 → h2 → h3
□ aria-live="polite" on async status regions
□ Contrast ≥ 4.5:1 body, ≥ 3:1 large text
```

## Delivery pattern
1. `index.html` — semantic structure
2. `styles.css` — tokens → base → layout → components → utilities
3. `app.js` — behaviour only, no inline handlers
4. Open it and screenshot/verify before declaring done.

## Anti-patterns
❌ Fixed pixel widths · ❌ `!important` chains · ❌ 12px body text ·
❌ Placeholder used as label · ❌ Carousels for key content ·
❌ Auto-playing audio/video · ❌ Custom scrollbars that hide affordance ·
❌ Blocking the render on webfonts

## ⛔ QUALITY GATES (critic inhe grep karke check karega — koi bhi fail = task fail)

Deliverable me ye sab HONI chahiye — "minimal" version pass nahi hoga:

1. `:root{...}` design tokens block — colors + type scale + spacing + radius (min 12 variables)
2. `@media` breakpoint — kam se kam 1 (mobile-first enhancement)
3. `:hover` AND `:focus-visible` states — buttons/inputs/links dono par
4. `transition` — kam se kam 2 jagah (micro-interaction)
5. `min-height:44px` (ya usse bada tap target) interactive elements par
6. Semantic HTML: `<header> <main> <button>` — div-soup mana hai
7. `aria-label` / `role` jahan visual label nahi (icon buttons)
8. `prefers-reduced-motion` media query respect
9. CSS `min-width: 120` lines (single-file HTML me `<style>` ke andar) — thin/lazy CSS fail
10. `box-sizing:border-box` reset + `font-family` system stack
11. Layout: flex ya grid — dono nahi to absolute positioning mana hai
12. Empty/loading state handling (JS apps me) — "kabhi blank screen nahi"

## Self-check before claiming done
- [ ] Kya maine design doc/research ki recommendations me se kam se kam 5 laagu kiye?
- [ ] Kya sab 12 quality gates pass hote hain?
- [ ] Kya maine file ko kholum aur har gate ko grep kiya?
Aisa mat karo: basic HTML + 20 lines CSS likh ke "modern responsive UI" claim kar dena.
