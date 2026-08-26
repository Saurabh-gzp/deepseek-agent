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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

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
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:2500]
            except Exception:
                continue
            fm = _parse_frontmatter(head)
            rel = p.relative_to(self.dir)
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
        q = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for s in self.skills.values():
            hay = set(re.findall(r"[a-z0-9]+",
                                 f"{s.id} {s.name} {s.description} {' '.join(s.tags)}".lower()))
            overlap = len(q & hay)
            if overlap:
                scored.append((overlap / (len(q) or 1), s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:limit]]

    def load_body(self, skill_id: str, max_chars: int = 12000) -> str:
        s = self.get(skill_id)
        if not s:
            avail = ", ".join(sorted(self.skills)[:25])
            return f"Skill '{skill_id}' not found. Available: {avail}"
        body = s.load()
        refs = self._refs(s)
        head = f"# SKILL: {s.name} ({s.id})\n"
        if refs:
            head += f"_Reference files available (use read_file): {', '.join(refs)}_\n\n"
        return head + body[:max_chars]

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
