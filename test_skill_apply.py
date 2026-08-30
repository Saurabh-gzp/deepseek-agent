"""load_skill must EXECUTE ui_ux_pro_max (design-system tokens), not truncate it."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deepseek_agent.skills.loader import SkillLibrary


class ApplySkill(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent
        self.lib = SkillLibrary(root / "skills")

    def test_ui_skill_returns_hex_tokens(self):
        tmp = Path(tempfile.mkdtemp())
        body, tokens = self.lib.apply_for_task(
            "web_development/ui_ux_pro_max",
            "make a best portfolio website for yourself and host locally",
            persist_dir=tmp,
        )
        self.assertIsNotNone(tokens, body[:400])
        colors = (tokens or {}).get("colors") or {}
        self.assertTrue(any(str(v).startswith("#") for v in colors.values()), colors)
        self.assertIn("--color-primary", body)
        self.assertIn("DESIGN TOKENS", body)
        self.assertNotIn("[skill truncated]", body)
        self.assertNotIn("NOW IMPLEMENT", body)
        # persist DESIGN.md so the agent can read_file it
        md = tmp / "DESIGN.md"
        self.assertTrue(md.exists(), "DESIGN.md not written")
        text = md.read_text(encoding="utf-8")
        self.assertIn("--color-primary", text)
        heading = ((tokens or {}).get("typography") or {}).get("heading") or ""
        if heading:
            self.assertIn(heading.split()[0], body)

    def test_task_query_keeps_portfolio(self):
        q = self.lib._task_query(
            "make a best portfolio website for yourself and host kr dena locally")
        self.assertIn("portfolio", q.lower())
        self.assertIn("website", q.lower())
        self.assertNotIn("dena", q.lower())

    def test_missing_skill(self):
        body, tokens = self.lib.apply_for_task("no/such/skill")
        self.assertIsNone(tokens)
        self.assertIn("not found", body.lower())


class TokenNudge(unittest.TestCase):
    def test_generic_css_is_rejected(self):
        from deepseek_agent.agents.base import BaseAgent

        class Dummy:
            state = {"design_system": {
                "colors": {"primary": "#1E293B", "accent": "#22C55E",
                           "background": "#0F172A"},
                "typography": {"heading": "JetBrains Mono"},
            }}

        class A(BaseAgent):
            def __init__(self):
                self.ctx = Dummy()

        a = A()
        miss = a._nudge_if_tokens_ignored({
            "path": "portfolio/style.css",
            "content": "body{background:#0a0a0a;color:#fff} /* purple glass */",
        })
        self.assertTrue(miss)
        ok = a._nudge_if_tokens_ignored({
            "path": "portfolio/style.css",
            "content": ":root{--color-primary:#1E293B;--color-accent:#22C55E}"
                       "h1{font-family:'JetBrains Mono'}",
        })
        self.assertEqual(ok, "")


if __name__ == "__main__":
    unittest.main()
