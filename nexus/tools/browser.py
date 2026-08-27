"""Playwright-based browser tools — REAL browser automation (JS SPAs, logins).

Stateful: all tools share ONE persistent browser session (context + page),
so an agent can navigate -> inspect -> fill -> click across calls.

Graceful degradation: if playwright is not installed, every tool returns a
clear setup error instead of crashing the agent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .base import Risk, ToolRegistry, ToolResult
from .ssrf import url_blocked
from .paths import in_workspace
import threading

try:
    from playwright.sync_api import sync_playwright, Error as PWError
    _PW_AVAILABLE = True
except Exception:                                            # noqa: BLE001
    sync_playwright = None
    PWError = Exception
    _PW_AVAILABLE = False

_SETUP_HINT = ("playwright missing — run: pip install playwright && "
               "python3 -m playwright install chromium")

_MAX_TEXT = 6000


class BrowserSession:
    """Process-wide singleton: one playwright driver, one context, one page."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page: Optional[object] = None

    def page(self):
        if not _PW_AVAILABLE:
            raise RuntimeError(_SETUP_HINT)
        if self._page is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"])
            ctx = self._browser.new_context(
                user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
                viewport={"width": 1366, "height": 900},
                locale="en-US")
            self._page = ctx.new_page()
        return self._page

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._page = None


# v1.9.9 B3 FIX: thread-local browser sessions. Parallel tasks run in
# ThreadPoolExecutor worker threads — a global singleton made agent A read
# agent B's page (cross-task contamination). Each thread now owns a session;
# sequential tasks on the same thread still reuse it (desired state carry-over).
_TLS = threading.local()
_ALL_SESSIONS: list = []
_SESS_LOCK = threading.Lock()


def _current_session() -> "BrowserSession":
    s = getattr(_TLS, "session", None)
    if s is None:
        s = BrowserSession()
        _TLS.session = s
        with _SESS_LOCK:
            _ALL_SESSIONS.append(s)
    return s


def close_all_sessions() -> None:
    with _SESS_LOCK:
        for s in _ALL_SESSIONS:
            try:
                s.close()
            except Exception:
                pass
        _ALL_SESSIONS.clear()


def _wrap(fn) -> ToolResult:
    try:
        return fn()
    except RuntimeError as e:
        return ToolResult(False, error=str(e))
    except PWError as e:
        msg = str(e).split("\n")[0][:300]
        return ToolResult(False, error=f"browser error: {msg}")
    except Exception as e:                                   # noqa: BLE001
        return ToolResult(False, error=f"browser error: {str(e)[:300]}")


def _describe_page(page, max_chars: int = _MAX_TEXT) -> str:
    title = page.title() or ""
    url = page.url
    text = page.inner_text("body") if page.query_selector("body") else ""
    text = " ".join(text.split())[:max_chars]
    return f"URL: {url}\nTITLE: {title}\n\nPAGE TEXT:\n{text or '(empty)'}"


class BrowserTools:
    """Registered as nexus tools; all share _SESSION."""

    def __init__(self, workspace: Path = None) -> None:
        # screenshots with relative paths resolve against the WORKSPACE root
        # (same contract as run_shell/write_file), NOT the process CWD.
        # Live bug: 'login_page.png' landed next to nexus.py and the critic
        # (rightly) failed the task — file missing from the workspace.
        self.root = Path(workspace).resolve() if workspace else Path.cwd()

    # ------------------------------------------------------------------
    def navigate(self, url: str, wait_seconds: float = 3.0,
                 wait_for: str = "", **kw) -> ToolResult:
        def go():
            # v1.9.9 B1 FIX: the browser MUST obey the same SSRF policy as the
            # web tools (live audit: browser_navigate bypassed url_blocked and
            # could reach 127.0.0.1/metadata — SSRF hole through the new tool).
            why = url_blocked(url)
            if why:
                return ToolResult(False, error=f"SSRF blocked: {why}")
            page = _current_session().page()
            requested = url.strip()

            # per-request interception: blocks redirects/fetches into private nets
            def _guard(route):
                w = url_blocked(route.request.url)
                if w:
                    return route.abort()
                return route.continue_()
            try:
                page.route("**/*", _guard)
            except Exception:
                pass

            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # post-load check: final URL after redirects must also be clean
            why2 = url_blocked(page.url)
            if why2:
                try:
                    page.go_back()
                except Exception:
                    pass
                return ToolResult(False, error=(
                    f"SSRF blocked: navigation ended on a forbidden target ({why2})"))
            try:
                page.wait_for_load_state("networkidle", timeout=int(wait_seconds * 1000))
            except Exception:
                pass
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=int(wait_seconds * 1000))
                except Exception:
                    pass
            # v1.9.8 WRONG-LAND AUTO-RETRY: if we landed somewhere that is NOT the
            # requested host (and not a subdomain of it), retry the EXACT url once.
            # (Live: coder asked for mistral.ai/login, got the marketing page and
            #  wrote a full 'analysis' of the wrong page.)
            from urllib.parse import urlparse as _up
            def _landed_ok(req: str) -> bool:
                hr = (_up(req).netloc or "").lower()
                hg = (_up(page.url).netloc or "").lower()
                return (not hr) or hg == hr or hg.endswith("." + hr)
            retry_note = ""
            if not _landed_ok(requested):
                page.goto(requested, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=int(wait_seconds * 1000))
                except Exception:
                    pass
                retry_note = ("(auto-retried the exact requested URL after a "
                              f"wrong redirect to {_up(page.url).netloc})")
            out = _describe_page(page)
            if retry_note:
                out = retry_note + "\n" + out
            # v1.9.8: SPA forms hydrate late — if no inputs yet, give them one more beat
            n_inputs = page.evaluate(
                "document.querySelectorAll('input,button[type=submit]').length")
            if not n_inputs and not wait_for:
                page.wait_for_timeout(3000)
                n_inputs = page.evaluate(
                    "document.querySelectorAll('input,button[type=submit]').length")
            try:
                inputs = page.evaluate(
                    """Array.from(document.querySelectorAll('input')).map(i =>
                        ({name: i.name || i.id || i.type, type: i.type,
                          visible: i.offsetParent !== null}))""")
            except Exception:
                inputs = []
            lines = ["", "INPUT FIELDS ON PAGE: " + (str(len(inputs)) if inputs else "0")]
            for i in (inputs or [])[:8]:
                lines.append(f"  - input[name={i.get('name')!r} type={i.get('type')!r}]")
            lines.append("(fill with browser_fill(selector='input[name=...]', text=...))")
            # v1.9.8: wrong-page tripwires — models guess URLs instead of the exact one
            from urllib.parse import urlparse as _up
            host_req, host_got = _up(requested).netloc, _up(page.url).netloc
            body_head = " ".join((page.inner_text("body") if page.query_selector("body")
                                  else "").split())[:300].lower()
            if host_req and host_got and host_req != host_got:
                lines.insert(0, f"WARNING: you requested {host_req} but landed on {host_got} "
                                f"(redirect). If this is not the login page, navigate to the "
                                f"EXACT URL given in the task.")
            if "404" in (page.title() or "") or "page not found" in body_head:
                lines.insert(0, "WARNING: this page looks like a 404 / not-found page. "
                                "You navigated to the WRONG URL — use the exact URL from "
                                "the task description, do not guess or shorten it.")
            return ToolResult(True, output=out + "\n" + "\n".join(lines))
        return _wrap(go)

    def click(self, selector: str, timeout_ms: int = 8000, **kw) -> ToolResult:
        def do():
            page = _current_session().page()
            el = page.locator(selector).first
            el.wait_for(state="visible", timeout=timeout_ms)
            el.click(timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return ToolResult(True, output=f"clicked {selector!r}\n\n" + _describe_page(page, 3000))
        return _wrap(do)

    def fill(self, selector: str, text: str, submit: bool = False, **kw) -> ToolResult:
        def do():
            page = _current_session().page()
            el = page.locator(selector).first
            el.wait_for(state="visible", timeout=8000)
            el.fill(text)
            out = f"filled {selector!r} with {len(text)} chars"
            if submit:
                before_url, before_txt = page.url, page.inner_text("body")[:200]
                el.press("Enter")
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # v1.9.8: SPA navigations finish AFTER networkidle â poll up to 8s
                # for a visible page change so the returned state is the REAL one.
                for _ in range(8):
                    cur_txt = ""
                    try:
                        cur_txt = page.inner_text("body")[:200]
                    except Exception:
                        pass
                    if page.url != before_url or (cur_txt and cur_txt != before_txt):
                        break
                    page.wait_for_timeout(1000)
                out += " + Enter submitted"
            return ToolResult(True, output=out + "\n\n" + _describe_page(page, 3000))
        return _wrap(do)

    def snapshot(self, path: str = "screenshots/page.png", full_page: bool = False, **kw) -> ToolResult:
        def do():
            page = _current_session().page()
            # v1.9.9 B2 FIX: sandbox EVERY resolved path inside the workspace —
            # absolute paths and ../ traversal previously escaped the sandbox.
            raw = Path(path)
            p = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
            if not in_workspace(p, self.root):
                return ToolResult(False, error=(
                    f"BLOCKED: screenshot path escapes the workspace sandbox: {path} "
                    f"(resolved {p}). Use paths inside {self.root}."))
            p.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(p), full_page=bool(full_page))
            try:
                rel = p.relative_to(self.root)
                loc = str(rel)
            except Exception:
                loc = str(p)
            return ToolResult(True, output=f"screenshot saved (workspace-relative): {loc} "
                                           f"({p.stat().st_size} bytes)")
        return _wrap(do)

    def content(self, max_chars: int = _MAX_TEXT, selector: str = "body", **kw) -> ToolResult:
        def do():
            page = _current_session().page()
            if selector == "body":
                return ToolResult(True, output=_describe_page(page, max_chars))
            el = page.locator(selector).first
            txt = el.inner_text() if el.count() else "(selector not found)"
            return ToolResult(True, output=f"URL: {page.url}\n\n" + " ".join(txt.split())[:max_chars])
        return _wrap(do)

    def eval_js(self, expression: str, **kw) -> ToolResult:
        def do():
            page = _current_session().page()
            val = page.evaluate(expression)
            out = json.dumps(val, default=str) if not isinstance(val, str) else val
            return ToolResult(True, output=str(out)[:4000])
        return _wrap(do)

    def close(self, close_all: bool = False, **kw) -> ToolResult:
        if close_all:
            close_all_sessions()
            return ToolResult(True, output="ALL browser sessions closed")
        _current_session().close()
        return ToolResult(True, output="browser session closed (this thread)")

    # ------------------------------------------------------------------
    def register(self, reg: ToolRegistry) -> None:
        S = {"type": "string"}
        B = {"type": "boolean"}
        N = {"type": "number"}
        if not _PW_AVAILABLE:
            return
        common = ["supervisor", "coder", "worker", "solo"]
        reg.add("browser_navigate",
                "Open a URL in the REAL headless Chromium (handles JS SPAs, renders "
                "login pages, passes basic bot checks). Returns URL + title + visible "
                "page text. Use for login-flow automation and JS-heavy sites where "
                "web_fetch returns 403/empty HTML.",
                {"type": "object", "properties": {"url": S, "wait_seconds": N},
                 "required": ["url"]},
                self.navigate, Risk.NETWORK, agents=common)
        reg.add("browser_fill",
                "Fill a form field in the live browser session. selector = CSS or "
                "playwright selector (e.g. \"input[name='email']\"). submit=true "
                "presses Enter after filling. Returns the resulting page state. "
                "ALWAYS compare the returned page text with before: if the page did "
                "NOT change, the submit didn't fire â then browser_click the actual "
                "submit button (e.g. 'button:has-text(\"Continue\")').",
                {"type": "object", "properties": {
                    "selector": S, "text": S, "submit": B}, "required": ["selector", "text"]},
                self.fill, Risk.NETWORK, agents=common)
        reg.add("browser_click",
                "Click an element in the live browser session (CSS/playwright selector). "
                "Returns the resulting page state — use it for buttons/links/tabs.",
                {"type": "object", "properties": {
                    "selector": S, "timeout_ms": N}, "required": ["selector"]},
                self.click, Risk.NETWORK, agents=common)
        reg.add("browser_content",
                "Read the CURRENT page of the live browser session (URL + title + text). "
                "Use after navigate/click to see what changed. selector='' reads whole body.",
                {"type": "object", "properties": {
                    "selector": S, "max_chars": N}, "required": []},
                self.content, Risk.READ_ONLY, agents=common)
        reg.add("browser_screenshot",
                "Save a PNG screenshot of the current page as visual evidence "
                "(e.g. screenshots/login-step2.png).",
                {"type": "object", "properties": {
                    "path": S, "full_page": B}, "required": []},
                self.snapshot, Risk.READ_ONLY, agents=common)
        reg.add("browser_eval",
                "Run JavaScript in the current page (document.title, localStorage, "
                "fetch checks) and return the JSON result. Powerful — use sparingly.",
                {"type": "object", "properties": {"expression": S}, "required": ["expression"]},
                self.eval_js, Risk.NETWORK, agents=common)
        reg.add("browser_close",
                "Close the browser session (call when done to free memory).",
                {"type": "object", "properties": {}}, self.close, Risk.READ_ONLY, agents=common)
