"""Skill system with 3-level progressive disclosure.

Level 1: frontmatter (name/description)  -> hamesha system prompt me (~60 tokens/skill)
Level 2: SKILL body                       -> jab task match kare
Level 3: linked reference files           -> jab agent explicitly padhe

Directory layout (user-defined, nested allowed):
    skills/web_development/frontend_ui_ux_design.md
    skills/automation/webautomation/web_automation.md
    skills/plan/make_plan.md
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class Skill:
    id: str                    # "web_development/frontend_ui_ux_design"
    name: str
    description: str
    path: Path
    category: str = ""
    tags: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=lambda: ["*"])
    version: str = "1.0"
    body: str = ""
    loaded: bool = False
    max_body: int = 0

    def load(self) -> str:
        if not self.loaded:
            text = self.path.read_text(encoding="utf-8", errors="ignore")
            self.body = _strip_frontmatter(text)
            self.loaded = True
        return self.body

    def summary(self) -> str:
        return f"- `{self.id}` — {self.description}"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _parse_frontmatter(text: str) -> Dict:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    raw = parts[1]
    if yaml is not None:
        try:
            return yaml.safe_load(raw) or {}
        except Exception:
            pass
    out: Dict = {}
    for line in raw.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, v = line.split(":", 1)
            v = v.strip().strip('"\'')
            if v.startswith("[") and v.endswith("]"):
                out[k.strip()] = [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
            else:
                out[k.strip()] = v
    return out


class SkillLibrary:
    def __init__(self, skills_dir: Path, max_active: int = 3):
        self.dir = Path(skills_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_active = max_active
        self.skills: Dict[str, Skill] = {}
        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> int:
        self.skills.clear()
        for p in sorted(self.dir.rglob("*.md")):
            if p.name.upper() in ("README.MD",):
                continue
            # v1.10.0: bundle reference dirs are material, not skills
            if p.parent.name in ("references", "reference", "assets"):
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:2500]
            except Exception:
                continue
            fm = _parse_frontmatter(head)
            rel = p.relative_to(self.dir)
            # v1.10.0: bundle dir <cat>/<name>/<name>.md (or SKILL.md) IS the
            # skill <cat>/<name> — don't duplicate the dirname in the id.
            if p.stem == "SKILL" or p.stem == p.parent.name:
                rel = p.parent.relative_to(self.dir)
            sid = str(rel.with_suffix("")).replace("\\", "/")
            desc = str(fm.get("description") or _first_line(head) or sid)
            self.skills[sid] = Skill(
                id=sid,
                name=str(fm.get("name") or p.stem.replace("_", " ").title()),
                description=desc[:400],
                path=p,
                category=str(rel.parts[0]) if len(rel.parts) > 1 else "general",
                tags=list(fm.get("tags") or []),
                agents=list(fm.get("agents") or ["*"]),
                version=str(fm.get("version", "1.0")),
                max_body=int(fm.get("max-body-chars") or 0),
            )
        return len(self.skills)

    # ------------------------------------------------------------------
    def catalog(self, agent: Optional[str] = None) -> str:
        """Level 1 — index injected into every system prompt."""
        items = [s for s in self.skills.values()
                 if agent is None or "*" in s.agents or agent in s.agents]
        if not items:
            return ""
        by_cat: Dict[str, List[Skill]] = {}
        for s in items:
            by_cat.setdefault(s.category, []).append(s)
        lines: List[str] = []
        for cat in sorted(by_cat):
            lines.append(f"**{cat}**")
            lines.extend(s.summary() for s in sorted(by_cat[cat], key=lambda x: x.id))
        return "\n".join(lines)

    def get(self, skill_id: str) -> Optional[Skill]:
        if skill_id in self.skills:
            return self.skills[skill_id]
        sid = skill_id.strip().strip("/").removesuffix(".md")
        if sid in self.skills:
            return self.skills[sid]
        # fuzzy: match by suffix or name
        for k, s in self.skills.items():
            if k.endswith(sid) or s.name.lower() == sid.lower().replace("_", " "):
                return s
        return None

    def search(self, query: str, limit: int = 3) -> List[Skill]:
        # v1.10.0: weighted retrieval. The old raw token-overlap ranked by
        # junk ('a' matched any prose) and broke ties alphabetically, so
        # "Build a fintech dashboard" surfaced termux_environment instead of
        # the UI skill. Now: 2-char tokens only match explicit tags (so 'ui'
        # and 'ux' trigger their skill but prose junk like 'it'/'on' can't),
        # tag/name hits count double, ties break by id for determinism.
        toks = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 2]
        if not toks:
            return []
        scored = []
        for s in self.skills.values():
            hay = set(re.findall(r"[a-z0-9]+",
                                 f"{s.id} {s.name} {s.description} {' '.join(s.tags)}".lower()))
            strong = set(re.findall(r"[a-z0-9]+",
                                    f"{s.name} {' '.join(s.tags)}".lower()))
            score = 0.0
            for t in set(toks):
                if t in strong:
                    score += 2.0
                elif len(t) >= 3 and t in hay:
                    score += 1.0
            if score:
                scored.append((score / (len(toks) ** 0.5), s))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [s for _, s in scored[:limit]]

    def load_body(self, skill_id: str, max_chars: int = 12000) -> str:
        s = self.get(skill_id)
        if not s:
            avail = ", ".join(sorted(self.skills)[:25])
            return f"Skill '{skill_id}' not found. Available: {avail}"
        cap = s.max_body or max_chars
        body = s.load()
        # v1.10.0: bundled skills reference their own scripts/data — give them
        # a portable ${SKILL_DIR} the loader resolves to the real directory.
        sdir = str(s.path.parent.resolve())
        body = body.replace("${SKILL_DIR}", sdir)
        refs = self._refs(s)
        head = f"# SKILL: {s.name} ({s.id})\n"
        head += f"_Skill directory: `{sdir}`_\n"
        if refs:
            head += f"_Reference files available (use read_file): {', '.join(refs)}_\n\n"
        return head + body[:cap]

    def apply_for_task(self, skill_id: str, task: str = "",
                       persist_dir: Optional[Path] = None):
        """Load a skill AND execute its bundled design-system script when present.

        Live bug: dumping 16k of ui_ux_pro_max.md then truncating to 3500 chars
        cut off the mandatory `search.py --design-system` workflow. The model
        then invented a generic purple/glassmorphism theme. We run the script
        ourselves and return concrete tokens the CSS MUST use.
        """
        s = self.get(skill_id)
        if not s:
            return self.load_body(skill_id), None
        sdir = s.path.parent.resolve()
        search_py = sdir / "scripts" / "search.py"
        if search_py.exists():
            tokens, ds_block = self._run_design_system(search_py, task, persist_dir)
            playbook = self._compact_playbook(s, sdir)
            return playbook + ("\n\n" + ds_block if ds_block else ""), tokens
        return self.load_body(skill_id, max_chars=8000), None

    @staticmethod
    def _task_query(task: str) -> str:
        stop = {
            "the", "and", "for", "yourself", "myself", "with", "best", "make",
            "a", "an", "kr", "dena", "ke", "sath", "bnana", "banao", "bana",
            "host", "locally", "please", "karo", "kardo", "do", "me", "my",
            "your", "our", "this", "that", "then", "also",
        }
        words = [w for w in re.findall(r"[A-Za-z]{3,}", task or "")
                 if w.lower() not in stop]
        return " ".join(words[:8]) or "modern product website"

    def _run_design_system(self, search_py: Path, task: str,
                           persist_dir: Optional[Path]):
        query = self._task_query(task)
        name = "Site"
        m = re.search(r"\b(portfolio|dashboard|landing|saas|agency|studio)\b",
                      task or "", re.I)
        if m:
            name = m.group(1).title()
        cmd = [sys.executable, str(search_py), query, "--design-system", "--json",
               "-p", name]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25,
                                  cwd=str(search_py.parent))
        except Exception as e:  # noqa: BLE001
            return None, f"(design-system script failed to launch: {e})"
        raw = (proc.stdout or "").strip()
        tokens = None
        if raw:
            try:
                payload = json.loads(raw)
                tokens = payload.get("design_system") or payload
            except Exception:
                tokens = None
        if not tokens:
            ascii_cmd = [sys.executable, str(search_py), query, "--design-system",
                         "-p", name]
            try:
                proc2 = subprocess.run(ascii_cmd, capture_output=True, text=True,
                                       timeout=25, cwd=str(search_py.parent))
                txt = (proc2.stdout or "")[:4000]
            except Exception:
                txt = (proc.stderr or raw or "no output")[:1500]
            return None, (
                f"## DESIGN SYSTEM (script output for query {query!r})\n"
                f"Use the colours/fonts below. Do NOT invent a purple template.\n\n"
                f"```\n{txt}\n```")
        if persist_dir:
            try:
                persist_dir = Path(persist_dir)
                persist_dir.mkdir(parents=True, exist_ok=True)
                md = self._tokens_to_md(tokens, query)
                (persist_dir / "DESIGN.md").write_text(md, encoding="utf-8")
            except Exception:
                pass
        return tokens, self._tokens_to_md(tokens, query)

    @staticmethod
    def _tokens_to_md(tokens: dict, query: str) -> str:
        colors = tokens.get("colors") or {}
        typo = tokens.get("typography") or {}
        style = tokens.get("style") or {}
        pattern = tokens.get("pattern") or {}
        lines = [
            "## DESIGN TOKENS — MANDATORY (already generated for this task)",
            f"_query: {query}_",
            "",
            "Copy these EXACT values into `:root` of your CSS. A generic purple/",
            "indigo/glassmorphism template that ignores these hex codes is a FAIL.",
            "",
            f"**Style:** {style.get('name') or '?'}",
            f"**Pattern:** {pattern.get('name') or '?'}",
            f"**Effects:** {tokens.get('key_effects') or style.get('effects') or ''}",
            f"**Avoid:** {tokens.get('anti_patterns') or ''}",
            "",
            "```css",
            ":root {",
        ]
        cmap = [
            ("primary", "--color-primary"),
            ("on_primary", "--color-on-primary"),
            ("secondary", "--color-secondary"),
            ("accent", "--color-accent"),
            ("cta", "--color-accent"),
            ("background", "--color-background"),
            ("foreground", "--color-foreground"),
            ("card", "--color-card"),
            ("muted", "--color-muted"),
            ("muted_foreground", "--color-muted-foreground"),
            ("border", "--color-border"),
        ]
        seen = set()
        for key, var in cmap:
            val = colors.get(key)
            if val and var not in seen:
                lines.append(f"  {var}: {val};")
                seen.add(var)
        heading = typo.get("heading") or ""
        body = typo.get("body") or heading
        if heading:
            lines.append(f'  --font-heading: "{heading}", system-ui, sans-serif;')
        if body:
            lines.append(f'  --font-body: "{body}", system-ui, sans-serif;')
        lines.append("}")
        gfont = typo.get("css_import") or typo.get("google_fonts_url") or ""
        if gfont:
            if gfont.startswith("@import"):
                lines.append(gfont if gfont.endswith(";") else gfont + ";")
            else:
                lines.append(f"@import url('{gfont}');")
        lines.append("```")
        lines += [
            "",
            f"**Fonts:** heading `{heading}` / body `{body}`",
            f"**Mood:** {typo.get('mood') or ''}",
            "",
            "Pre-delivery: SVG icons (no emoji-as-icon), 44×44 touch targets, "
            "visible :focus-visible, prefers-reduced-motion, hover 150-300ms, "
            "responsive 375/768/1024. Put the CSS variables in the stylesheet "
            "BEFORE writing hero copy.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _compact_playbook(skill: Skill, sdir: Path) -> str:
        return (
            f"# SKILL: {skill.name} ({skill.id})\n"
            f"_Skill directory: `{sdir}`_\n\n"
            "This skill was EXECUTED for you (design-system search already ran).\n"
            "Do NOT recap the skill. Do NOT invent a palette. IMPLEMENT with "
            "write_file using the DESIGN TOKENS below.\n\n"
            "If you need extra guidance later:\n"
            f"  python3 {sdir}/scripts/search.py \"<query>\" --domain ux\n"
            f"  read_file {sdir}/references/pro-rules.md\n"
        )

    def _refs(self, skill: Skill) -> List[str]:
        d = skill.path.parent / f"{skill.path.stem}_refs"
        out = []
        if d.exists():
            out += [str(p.relative_to(self.dir)) for p in d.glob("*.md")]
        for name in ("references", "reference", "assets", "scripts"):
            sub = skill.path.parent / name
            if sub.exists():
                out += [str(p.relative_to(self.dir)) for p in sub.iterdir() if p.is_file()]
        return out[:12]

    def categories(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in self.skills.values():
            out[s.category] = out.get(s.category, 0) + 1
        return out

    def create_skill(self, skill_id: str, name: str, description: str, body: str,
                     tags: Optional[List[str]] = None) -> Path:
        p = self.dir / f"{skill_id.strip('/')}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        fm = ["---", f"name: {name}", f"description: {description}",
              f"tags: [{', '.join(tags or [])}]", "version: 1.0", "agents: [\"*\"]", "---", ""]
        p.write_text("\n".join(fm) + body.strip() + "\n", encoding="utf-8")
        self.reload()
        return p


def _first_line(text: str) -> str:
    for line in _strip_frontmatter(text).splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""
