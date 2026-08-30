"""Live E2E: login already done; build a website + files via the real agent."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from deepseek_agent.cli.app import DeepSeekApp


GOAL = (
    "Create a folder projects/e2e-arena. Write TWO real files with write_file:\n"
    "1) projects/e2e-arena/index.html — a complete HTML5 page with "
    "<title>E2E Arena Site</title> and <h1>Hello from DeepSeek-Agent</h1> "
    "and a link to css/style.css.\n"
    "2) projects/e2e-arena/css/style.css — body { font-family: sans-serif; "
    "background:#111; color:#eee; }\n"
    "Then call start_server with directory=projects/e2e-arena, port=8765, "
    "marker=E2E Arena Site.\n"
    "Do NOT claim the site is live unless start_server returned HTTP 200. "
    "Do NOT skip tool calls."
)


def main() -> int:
    log = []

    def note(msg: str) -> None:
        print(msg, flush=True)
        log.append(msg)

    app = DeepSeekApp(None, "mono", True, "never", str(ROOT / "workspace"))
    app.config.set("safety.approval_mode", "never")
    ds = app._deepseek
    if ds is None:
        note("FAIL: deepseek provider not active")
        return 2
    if not ds.has_token():
        note("FAIL: no token after login")
        return 2
    note(f"login OK · mode will auto-select · token present")

    before = []
    try:
        before = ds.list_remote_sessions()
        note(f"account chats before: {len(before)}")
        for s in before[:8]:
            note(f"  - {(s.get('id') or '')[:12]}  {(s.get('title') or '')[:50]}")
    except Exception as e:
        note(f"list_remote_sessions error: {e}")

    try:
        app.run_focused(GOAL)
    except Exception:
        note("run_focused crashed:\n" + traceback.format_exc())
        return 3

    ws = Path(app.config.workspace)
    idx = ws / "projects" / "e2e-arena" / "index.html"
    css = ws / "projects" / "e2e-arena" / "css" / "style.css"
    note("--- filesystem ---")
    note(f"index.html exists: {idx.exists()}  size={idx.stat().st_size if idx.exists() else 0}")
    note(f"style.css exists:  {css.exists()}  size={css.stat().st_size if css.exists() else 0}")
    if idx.exists():
        body = idx.read_text(encoding="utf-8", errors="replace")
        note(f"title present: {'E2E Arena Site' in body}")
        note(f"h1 present: {'Hello from DeepSeek-Agent' in body}")
        note("index head:\n" + body[:400])

    after = []
    try:
        after = ds.list_remote_sessions()
        note(f"account chats after: {len(after)}")
        created = ds.created_sessions()
        note(f"agent-created session ids: {created}")
        if created:
            ok = ds.delete_session(created[0])
            note(f"delete_session({created[0][:12]}): {ok}")
            gone = ds.list_remote_sessions()
            still = [s for s in gone if s.get("id") in created]
            note(f"deleted session still listed: {bool(still)}")
    except Exception as e:
        note(f"session cleanup error: {e}")

    files_ok = idx.exists() and css.exists() and "E2E Arena Site" in idx.read_text(
        encoding="utf-8", errors="replace")
    note("E2E RESULT: " + ("PASS" if files_ok else "FAIL (files missing or incomplete)"))
    (ROOT / "e2e_log.txt").write_text("\n".join(log), encoding="utf-8")
    return 0 if files_ok else 1


if __name__ == "__main__":
    sys.exit(main())
