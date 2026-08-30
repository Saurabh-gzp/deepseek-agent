#!/usr/bin/env python3
"""Live E2E: portfolio website + local host. Logs workflow + DeepSeek thread."""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / "e2e_portfolio.log"

GOAL = ("make a best portfolio website for yourself  and host kr dena "
        "locally best ui ke sath bnana")


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def list_chats(prov):
    try:
        remote = prov.list_remote_sessions()
    except Exception as e:
        log(f"list_chats error: {e}")
        return []
    rows = []
    for s in remote:
        rows.append({"id": s.get("id"), "title": (s.get("title") or "")[:80]})
    return rows


def main() -> int:
    LOG.write_text("", encoding="utf-8")
    from deepseek_agent.cli.app import DeepSeekApp, auto_select_mode
    from deepseek_agent.providers.deepseek import DeepSeekProvider

    app = DeepSeekApp(str(ROOT / "config" / "config.yaml"),
                      theme="mono", verbose=True, approval="never",
                      workspace=str(ROOT / "workspace"))
    app.config.set("autonomy.task_timeout_seconds", 900)
    app.config.set("autonomy.max_steps_per_agent", 16)
    app.config.set("safety.approval_mode", "never")

    orig_notify = app.ui.notify

    def notify(level, msg):
        log(f"[{level}] {msg}")
        try:
            orig_notify(level, msg)
        except Exception:
            pass

    app.ui.notify = notify
    app.ctx.notify = notify
    try:
        app.ctx.llm.notify = notify
    except Exception:
        pass
    prov = app._find_deepseek()
    if prov is None:
        log("FATAL: no deepseek provider")
        return 2
    if not prov.has_token():
        log("FATAL: no token")
        return 2

    orig_chat = DeepSeekProvider.chat

    def wrapped_chat(self, model, messages, tools=None, **params):
        nmsg = len(messages or [])
        last = ""
        for m in messages or []:
            if m.get("role") == "user":
                last = str(m.get("content") or "")[:120].replace("\n", " ")
        log(f"CHAT in session={self._session} parent={self._parent_id} "
            f"primed={getattr(self, '_primed', None)} msgs={nmsg} "
            f"last_user={last!r}")
        res = orig_chat(self, model, messages, tools=tools, **params)
        ncall = len(res.tool_calls or [])
        names = []
        for c in (res.tool_calls or []):
            names.append((c.get("function") or {}).get("name"))
        log(f"CHAT out session={self._session} parent={self._parent_id} "
            f"primed={getattr(self, '_primed', None)} "
            f"tools={ncall}{names} content_len={len(res.content or '')} "
            f"think_len={len((res.raw or {}).get('thinking') or '')}")
        return res

    DeepSeekProvider.chat = wrapped_chat

    before = list_chats(prov)
    log(f"CHATS BEFORE ({len(before)}): {json.dumps(before, ensure_ascii=False)}")
    log(f"mode auto={auto_select_mode(GOAL)}")
    log(f"GOAL: {GOAL}")

    t0 = time.time()
    try:
        app.run_focused(GOAL)
    except Exception:
        log("RUN CRASH:\n" + traceback.format_exc())
    elapsed = time.time() - t0
    log(f"RUN elapsed={elapsed:.1f}s")

    log(f"provider session={prov.current_session()} "
        f"parent={prov._parent_id} primed={getattr(prov, '_primed', None)} "
        f"created={prov.created_sessions()}")
    after = list_chats(prov)
    log(f"CHATS AFTER ({len(after)}): {json.dumps(after, ensure_ascii=False)}")

    ws = Path(app.config.workspace)
    files = sorted(p.relative_to(ws).as_posix()
                   for p in ws.rglob("*") if p.is_file()
                   and ".deepseek" not in p.parts
                   and not p.name.startswith(".server_"))
    log(f"WORKSPACE FILES ({len(files)}): {files}")
    for html in ws.rglob("index.html"):
        text = html.read_text(encoding="utf-8", errors="replace")
        log(f"HTML {html.relative_to(ws)} bytes={len(text)} "
            f"title={( __import__('re').search(r'<title>([^<]+)', text, __import__('re').I) or type('x',(),{'group':lambda *a: '?'})() ).group(1)}")
    ds = app.ctx.state.get("design_system")
    log(f"design_system={json.dumps(ds, ensure_ascii=False)[:500] if ds else None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
