"""Live E2E: UI skill must inject design tokens AND the site must use them."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from deepseek_agent.cli.app import DeepSeekApp

GOAL = (
    "Make a best portfolio website for DeepSeek-Agent and host it locally "
    "with best UI. Put files in projects/portfolio-ds/ "
    "(index.html, css/style.css, js/main.js). "
    "FIRST load_skill web_development/ui_ux_pro_max — use its DESIGN TOKENS "
    "exactly in :root (do not invent a purple/glass template). "
    "Then start_server directory=projects/portfolio-ds port=8777 "
    "marker=DeepSeek-Agent."
)


def main() -> int:
    app = DeepSeekApp(None, "mono", True, "never", str(ROOT / "workspace"))
    app.config.set("safety.approval_mode", "never")
    app.config.set("autonomy.task_timeout_seconds", 420)
    ds = app._deepseek
    if ds is None or not ds.has_token():
        print("FAIL: no DeepSeek token")
        return 2
    print("login OK", flush=True)

    # Prove load_skill itself now returns tokens (no live model needed).
    app.ctx.state["current_task"] = GOAL
    r = app.ctx.tools.execute(
        "load_skill", {"skill_id": "web_development/ui_ux_pro_max"}, "solo")
    print("load_skill ok", r.ok, "len", len(r.output or ""), flush=True)
    print((r.output or "")[:500], flush=True)
    tokens = app.ctx.state.get("design_system") or {}
    colors = tokens.get("colors") or {}
    primary = str(colors.get("primary") or "")
    print("primary token", primary, flush=True)
    if not primary.startswith("#"):
        print("FAIL: load_skill did not produce hex tokens")
        return 3

    try:
        app.run_focused(GOAL)
    except Exception:
        print("run_focused crashed:\n" + traceback.format_exc())
        return 4

    ws = Path(app.config.workspace)
    css_files = list(ws.rglob("*.css"))
    html_files = list((ws / "projects" / "portfolio-ds").rglob("*.html")) if (
        ws / "projects" / "portfolio-ds").exists() else list(ws.rglob("index.html"))
    blob = ""
    for p in css_files + html_files:
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    design = ws / "DESIGN.md"
    print("--- checks ---")
    print("DESIGN.md", design.exists(), "css files", [str(p.relative_to(ws)) for p in css_files])
    print("html files", [str(p.relative_to(ws)) for p in html_files])
    print("primary in site", primary.lower() in blob.lower())
    heading = ((tokens.get("typography") or {}).get("heading") or "").split()
    if heading:
        print("font in site", heading[0].lower() in blob.lower())
    ok = bool(html_files) and bool(css_files) and primary.lower() in blob.lower()
    print("E2E RESULT:", "PASS" if ok else "FAIL")
    # stop server if we started one
    try:
        app.ctx.tools.execute("stop_server", {"port": 8777}, "solo")
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
