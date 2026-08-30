"""DeepSeek web-API provider for **DeepSeek-Agent**.

Talks to the SAME endpoints as the official DeepSeek web app
(https://chat.deepseek.com/api/v0/...):

  * email + password  ->  bearer token
      - direct HTTP first (works when AWS WAF is not challenging this IP)
      - headless Chromium (Playwright) fallback to pass the AWS WAF JS challenge
        (auto-detected: only used when playwright is installed)
      - on INVALID_TOKEN / 401 the provider auto re-logs-in to regenerate the
        token using the stored email+password (the "refresh" flow)
  * Proof-of-Work (PoW) sha3 challenge  ->  solved with Node.js + a WASM engine
  * streaming chat completion with thinking / web-search / native modes
    (instant / expert / vision)

DeepSeek's web API has NO native function-calling, so we implement **text-based
tool calling**: the model is instructed to emit
``TOOL_CALL: {"name": "...", "arguments": {...}}`` inside its reply, and the
provider parses that into OpenAI-style ``tool_calls`` so the BaseAgent ReAct
loop (and the whole DeepSeek-Agent engine) works unchanged.

NOTE: DeepSeek has no public embeddings / moderation / OCR endpoints, so
``supports_embeddings`` etc. are False — the DeepSeek-Agent RAG automatically degrades
to keyword search and safety skips LLM moderation.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import requests  # DeepSeek client uses `requests` (same as the working reference script)

IncompleteRead = http.client.IncompleteRead

from .base import BaseProvider, ChatResult, ProviderError
from .dsml import (build_dsml_tool_prompt, extract_dsml_calls, format_dsml_calls,
                   looks_like_dsml, strip_dsml)

# ---------------------------------------------------------------------------
# Native-app mode rules (mirrors the official DeepSeek app constraints)
# ---------------------------------------------------------------------------
MODES = {
    "default":  {  # INSTANT
        "model_type": "default",
        "supports_thinking": True,
        "supports_search": True,
        "supports_files": True,
        "label": "INSTANT",
    },
    "expert": {
        "model_type": "expert",
        "supports_thinking": True,
        "supports_search": False,
        "supports_files": False,
        "label": "EXPERT",
    },
    "vision": {
        "model_type": "vision",
        "supports_thinking": True,
        "supports_search": False,
        "supports_files": True,
        "label": "VISION",
    },
}
DEFAULT_MODE = "expert"

_WASM_NAME = "sha3_wasm_bg.7b9ca65ddd.wasm"
_WASM_URL = ("https://raw.githubusercontent.com/xtekky/deepseek4free/main/dsk/"
             "wasm/sha3_wasm_bg.7b9ca65ddd.wasm")
_SOLVER_NAME = "pow_solver.js"

_BASE = "https://chat.deepseek.com/api/v0"

# The official Android-app UA bypasses the AWS WAF (the desktop web UA gets
# a `challenge` response and 202). The working reference client uses this UA.
_UA = "DeepSeek/2.0.2 (Android; API)"

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": _UA,
    "X-Client-Platform": "web",
    "X-Client-Version": "2.0.2",
    "Origin": "https://chat.deepseek.com",
    "Referer": "https://chat.deepseek.com/",
}

# ---------------------------------------------------------------------------
# Token management (email+password -> token, cached, auto-refresh)
# ---------------------------------------------------------------------------
class DeepSeekAccount:
    """Stores email/password + cached bearer token with chmod 600 (device-only)."""

    def __init__(self, keys_dir: Path, notify: Callable[[str, str], None]):
        self.keys_dir = Path(keys_dir)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.notify = notify
        self.account_file = self.keys_dir / "deepseek_account.json"
        self.token_file = self.keys_dir / "deepseek_token"
        self._token: Optional[str] = None

    # -- storage ----------------------------------------------------------
    def save_account(self, email: str, password: str) -> None:
        try:
            self.account_file.write_text(
                json.dumps({"email": email, "password": password}),
                encoding="utf-8")
            os.chmod(self.account_file, 0o600)
        except Exception as e:
            self.notify("warn", f"could not save account: {e}")

    def load_account(self) -> Optional[Dict[str, str]]:
        try:
            if self.account_file.exists():
                d = json.loads(self.account_file.read_text(encoding="utf-8"))
                if d.get("email") and d.get("password"):
                    return d
        except Exception:
            pass
        return None

    def save_token(self, token: str) -> None:
        try:
            self.token_file.write_text(token, encoding="utf-8")
            os.chmod(self.token_file, 0o600)
        except Exception as e:
            self.notify("warn", f"could not save token: {e}")

    def load_cached_token(self) -> Optional[str]:
        if self._token:
            return self._token
        try:
            if self.token_file.exists():
                t = self.token_file.read_text(encoding="utf-8").strip()
                if t:
                    self._token = t
                    return t
        except Exception:
            pass
        return None

    # -- token lifecycle --------------------------------------------------
    def ensure_token(self, interactive: bool = True,
                     paste_callback: Optional[Callable[[], str]] = None) -> str:
        """Return a working token: cached -> login -> prompt to paste."""
        tok = self.load_cached_token()
        if tok and self._probe(tok):
            return tok
        # cached token bad/missing -> login with stored creds
        acct = self.load_account()
        if acct:
            self.notify("ok", "re-logging in with saved DeepSeek credentials…")
            new = http_login(acct["email"], acct["password"])
            if new:
                self._token = new
                self.save_token(new)
                return new
            # HTTP blocked (WAF) -> try headless browser (skips itself on Termux)
            new = browser_login(acct["email"], acct["password"], notify=self.notify)
            if new:
                self._token = new
                self.save_token(new)
                return new
        if interactive and paste_callback:
            self.notify("warn", "Login blocked by DeepSeek's WAF. Paste a token instead.")
            pasted = paste_callback()
            if pasted:
                self._token = pasted.strip()
                self.save_token(self._token)
                return self._token
        raise ProviderError(
            "No valid DeepSeek token. Add email+password (first-run setup) or paste a "
            "token. DeepSeek's AWS WAF may be blocking login — a pasted token from your "
            "browser always works.", retryable=False)

    def refresh(self, interactive: bool = False,
                paste_callback: Optional[Callable[[], str]] = None) -> str:
        """Force a new token (called on INVALID_TOKEN/401)."""
        self._token = None
        return self.ensure_token(interactive=interactive, paste_callback=paste_callback)

    def _probe(self, token: str) -> bool:
        try:
            r = requests.get(
                f"{_BASE}/chat_session/fetch_page",
                headers={**_HEADERS, "Authorization": f"Bearer {token}"},
                timeout=12)
            if r.status_code != 200:
                return False
            return r.json().get("code") == 0
        except Exception:
            return False


def is_termux() -> bool:
    """True when running under Termux (Android), where Playwright/Chromium can't run."""
    try:
        prefix = os.environ.get("PREFIX", "")
        if prefix and "com.termux" in prefix:
            return True
        if os.path.exists("/data/data/com.termux/files/usr/bin"):
            return True
    except Exception:
        pass
    return False


def http_login(email: str, password: str) -> Optional[str]:
    """Direct HTTP login using the Android UA (bypasses the AWS WAF).

    Same approach as the working reference DeepSeek client. Returns token or None.
    """
    try:
        login_headers = {
            "Content-Type": "application/json",
            "User-Agent": _UA,
            "Origin": "https://chat.deepseek.com",
            "Referer": "https://chat.deepseek.com/",
        }
        payload = {
            "email": email, "password": password,
            "device_id": str(uuid.uuid4()), "os": "Android",
        }
        resp = requests.post(f"{_BASE}/users/login", headers=login_headers,
                             json=payload, timeout=20)
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("code") == 0:
            tok = (body.get("data", {}).get("biz_data", {})
                   .get("user", {}).get("token"))
            return tok or None
    except Exception:
        return None
    return None


def browser_login(email: str, password: str, notify: Optional[Callable[[str, str], None]] = None) -> Optional[str]:
    """Headless-Chromium login to pass the AWS WAF JS challenge (needs playwright).

    Returns None (never raises) if Playwright/Chromium is unavailable — e.g. on
    Termux — so the caller can fall back to pasting a token. No scary tracebacks.
    """
    # Playwright/Chromium cannot run on Termux — skip silently.
    if is_termux():
        if notify:
            notify("warn", "browser login not available on Termux — use a pasted token instead")
        return None
    try:
        import playwright
        from playwright.async_api import async_playwright
    except Exception:
        return None
    # Guard: the bundled Playwright driver `node` binary must exist, otherwise
    # launching spawns a broken subprocess and spews an asyncio traceback.
    try:
        _driver_node = os.path.join(os.path.dirname(playwright.__file__), "driver", "node")
        if not os.path.exists(_driver_node):
            if notify:
                notify("warn", "Playwright driver missing — use a pasted token instead")
            return None
    except Exception:
        return None
    result: Dict[str, Any] = {}
    import asyncio

    async def _run():
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            except Exception:
                return
            ctx = await browser.new_context(user_agent=_HEADERS["User-Agent"])
            page = await ctx.new_page()
            found: List[str] = []

            async def on_resp(resp):
                try:
                    if "login" in resp.url and "json" in resp.headers.get("content-type", ""):
                        body = await resp.json()
                        def find(o):
                            if isinstance(o, dict):
                                for k, v in o.items():
                                    if isinstance(v, str) and len(v) > 40 and k in ("token", "biz_token"):
                                        found.append(v)
                                    find(v)
                            elif isinstance(o, list):
                                for x in o:
                                    find(x)
                        find(body)
                except Exception:
                    pass
            page.on("response", on_resp)
            try:
                await page.goto("https://chat.deepseek.com/sign_in", timeout=60000,
                                wait_until="domcontentloaded")
            except Exception:
                pass
            for _ in range(20):
                await asyncio.sleep(2)
                inputs = await page.query_selector_all("input")
                if len(inputs) >= 2:
                    break
            else:
                await browser.close()
                return
            inputs = await page.query_selector_all("input")
            await inputs[0].fill(email)
            await inputs[1].fill(password)
            await asyncio.sleep(0.3)
            for btn in await page.query_selector_all("button"):
                if (await btn.inner_text()).strip().lower() in ("log in", "sign in", "login", "continue"):
                    await btn.click()
                    break
            else:
                await page.keyboard.press("Enter")
            for _ in range(30):
                await asyncio.sleep(1)
                if found:
                    break
            result["token"] = found[0] if found else None
            await browser.close()

    try:
        asyncio.run(_run())
    except Exception:
        return None
    return result.get("token")


# ---------------------------------------------------------------------------
# Proof-of-Work (sha3) challenge solver via Node.js + WASM
# ---------------------------------------------------------------------------
_SOLVER_JS = r"""
const fs = require('fs');
const WASM_PATH = process.env.DS_WASM_PATH || 'sha3_wasm_bg.7b9ca65ddd.wasm';
async function main() {
  const config = JSON.parse(process.argv[2]);
  const buf = fs.readFileSync(WASM_PATH);
  const mod = await WebAssembly.compile(buf);
  const inst = await WebAssembly.instantiate(mod, {});
  const mem = inst.exports.memory;
  const prefix = `${config.salt}_${config.expire_at}_`;
  function w(s) {
    const e = Buffer.from(s, 'utf-8'); const n = e.length;
    const p = inst.exports.__wbindgen_export_0(n, 1);
    new Uint8Array(mem.buffer).set(e, p); return {ptr:p, length:n};
  }
  const rp = inst.exports.__wbindgen_add_to_stack_pointer(-16);
  try {
    const c = w(config.challenge), pf = w(prefix);
    inst.exports.wasm_solve(rp, c.ptr, c.length, pf.ptr, pf.length, config.difficulty);
    const st = new Int32Array(mem.buffer)[rp/4];
    if (st === 0) process.exit(1);
    const val = new Float64Array(mem.buffer)[(rp+8)/8];
    const out = {algorithm:config.algorithm, challenge:config.challenge, salt:config.salt,
      answer:Math.floor(val), signature:config.signature, target_path:config.target_path};
    console.log(Buffer.from(JSON.stringify(out)).toString('base64'));
  } finally { inst.exports.__wbindgen_add_to_stack_pointer(16); }
}
main().catch(()=>process.exit(1));
"""


class PoWSolver:
    def __init__(self, data_dir: Path, notify: Callable[[str, str], None]):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.notify = notify
        self.wasm = self.data_dir / _WASM_NAME
        self.solver = self.data_dir / _SOLVER_NAME
        self._lock = threading.Lock()
        if not self.solver.exists():
            try:
                self.solver.write_text(_SOLVER_JS, encoding="utf-8")
            except Exception:
                pass

    def _ensure_wasm(self) -> None:
        if self.wasm.exists():
            return
        with self._lock:
            if self.wasm.exists():
                return
            try:
                self.notify("warn", "downloading DeepSeek PoW engine (one-time)…")
                r = requests.get(_WASM_URL, timeout=60)
                r.raise_for_status()
                self.wasm.write_bytes(r.content)
            except Exception as e:
                raise ProviderError(f"could not fetch PoW WASM: {e}", retryable=False)

    def solve(self, token: str, target_path: str) -> str:
        self._ensure_wasm()
        try:
            resp = requests.post(
                f"{_BASE}/chat/create_pow_challenge",
                headers={**_HEADERS, "Authorization": f"Bearer {token}"},
                json={"target_path": target_path}, timeout=20)
            data = resp.json()
            ch = data["data"]["biz_data"]["challenge"]
        except Exception as e:
            raise ProviderError(f"PoW challenge failed: {e}")
        env = dict(os.environ)
        env["DS_WASM_PATH"] = str(self.wasm)
        try:
            r = subprocess.run(["node", str(self.solver), json.dumps(ch)],
                               capture_output=True, text=True, env=env, timeout=30,
                               cwd=str(self.data_dir))
            out = (r.stdout or "").strip()
            if not out:
                raise ProviderError("PoW solver returned nothing")
            return out
        except FileNotFoundError:
            raise ProviderError("node.js is required for DeepSeek (install: pkg install nodejs)",
                                retryable=False)
        except Exception as e:
            raise ProviderError(f"PoW solve failed: {e}")


# ---------------------------------------------------------------------------
# Text-based tool calling: the model emits TOOL_CALL JSON in its answer.
# ---------------------------------------------------------------------------
_TOOL_CALL_RE = re.compile(r"TOOL[\s_]*CALL\s*[:：]?\s*", re.IGNORECASE)
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*")


def _balanced_json(text: str, start: int) -> Optional[str]:
    """Return the balanced JSON object starting at text[start] == '{'.

    Handles nested braces and string escapes so trailing tokens (e.g.
    DeepSeek's ``FINISHED`` marker) don't break extraction.
    """
    n = len(text)
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def _strip_tool_calls(text: str) -> str:
    out = strip_dsml(text or "")
    # remove Claude-style <tool_calls>...</tool_calls> blocks and standalone <invoke>...
    out = _CALL_BLOCK_RE.sub("", out)
    out = _INVOKE_RE.sub("", out)
    out = _SELFCLOSE_INVOKE_RE.sub("", out)
    # remove TOOL CALL: [name] {json} spans
    spans = []
    for m in _TOOL_CALL_RE.finditer(out):
        after = m.end()
        nm = _NAME_RE.match(out, after)
        if nm and out[nm.end():nm.end() + 1] in (" ", "\t", "{", "("):
            after = nm.end()
        i = after
        while i < len(out) and out[i] in " \t:：=(":
            i += 1
        end = i
        if i < len(out) and out[i] == "{":
            block = _balanced_json(out, i)
            if block:
                end = i + len(block)
        spans.append((m.start(), end))
    for start, end in reversed(spans):
        out = out[:start] + out[end:]
    # drop DeepSeek's trailing FINISHED marker
    out = re.sub(r"\s*FINISHED\s*$", "", out)
    return out


# Claude/Anthropic-style XML tool calls the DeepSeek model sometimes emits:
#   <tool_calls><invoke name="write_file"><parameter name="path">x</parameter>...</invoke></tool_calls>
_INVOKE_RE = re.compile(r"<invoke\s+name=\"([^\"]+)\"\s*>(.*?)</invoke>", re.DOTALL)
_PARAM_RE = re.compile(r"<parameter\s+name=\"([^\"]+)\">(.*?)</parameter>", re.DOTALL)
_SELFCLOSE_INVOKE_RE = re.compile(r"<invoke\s+name=\"([^\"]+)\"\s*/>")

_CALL_BLOCK_RE = re.compile(r"<tool_calls>.*?</tool_calls>", re.DOTALL)


def _extract_xml_calls(text: str) -> List[dict]:
    calls: List[dict] = []
    for m in _INVOKE_RE.finditer(text or ""):
        name = m.group(1).strip()
        if not name:
            continue
        args: Dict[str, Any] = {}
        for pm in _PARAM_RE.finditer(m.group(2)):
            key = pm.group(1).strip()
            val = pm.group(2).strip()
            try:
                val = json.loads(val)      # parse numbers/bools/json
            except Exception:
                pass
            args[key] = val
        calls.append({
            "id": f"call_{int(time.time()*1000)}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    return calls


def _scan_marker_calls(text: str):
    """Yield (name, args_dict) for every `TOOL CALL: ...` occurrence.

    Supports both `TOOL CALL: name {"args":...}` and
    `TOOL CALL: {"name":...,"arguments":...}` layouts.
    """
    for m in _TOOL_CALL_RE.finditer(text or ""):
        after = m.end()
        name = ""
        nm = _NAME_RE.match(text, after)
        if nm and text[nm.end():nm.end() + 1] in (" ", "\t", "{", "("):
            name = nm.group(0)
            after = nm.end()
        # skip whitespace / colon to the JSON object
        i = after
        while i < len(text) and text[i] in " \t:：=(":
            i += 1
        if i >= len(text) or text[i] != "{":
            continue
        block = _balanced_json(text, i)
        if not block:
            continue
        try:
            obj = json.loads(block)
        except Exception:
            continue
        if not name:
            name = str(obj.get("name", ""))
        args = obj.get("arguments", obj if not obj.get("name") else {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"_raw": args}
        yield name, args


def _extract_tool_calls(text: str) -> List[dict]:
    """DSML (DeepSeek V4 native) first, then TOOL_CALL JSON, then Claude XML."""
    calls = extract_dsml_calls(text or "")
    if calls:
        return calls
    for name, args in _scan_marker_calls(text):
        if name and isinstance(args, dict):
            calls.append({
                "id": f"call_{int(time.time()*1000)}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            })
    calls.extend(_extract_xml_calls(text))
    return calls


def _mute_error(data: Any) -> str:
    """DeepSeek returns JSON {biz_code:5, biz_msg:user is muted} instead of SSE."""
    obj = None
    if isinstance(data, dict):
        obj = data
    elif isinstance(data, str):
        s = data.strip()
        if s.startswith("{") and "muted" in s.lower():
            try:
                obj = json.loads(s)
            except Exception:
                return "DeepSeek account is muted (completion blocked)."
        elif "user is muted" in s.lower():
            return "DeepSeek account is muted (completion blocked)."
    if not isinstance(obj, dict):
        return ""
    biz = obj.get("data") or {}
    msg = str(biz.get("biz_msg") or obj.get("msg") or "")
    code = biz.get("biz_code")
    if code == 5 or "muted" in msg.lower():
        until = (biz.get("biz_data") or {}).get("mute_until")
        extra = ""
        if until:
            try:
                extra = time.strftime(" until %Y-%m-%d %H:%M", time.localtime(float(until)))
            except Exception:
                extra = f" (until {until})"
        return f"DeepSeek account is muted{extra}. Wait it out or use another account."
    return ""


def _system_prompt(system: str, tools: Optional[List[dict]]) -> str:
    parts = [system or "You are DeepSeek-Agent, an autonomous agent."]
    if tools:
        parts.append(build_dsml_tool_prompt(tools))
    return "\n\n".join(parts)


def turn_prompt(primed: bool, system: str, tools: Optional[List[dict]],
                messages: List[dict]) -> str:
    """Build the *next* DeepSeek `prompt` field.

    chat.deepseek.com stores history on the session. Sending the full system
    prompt + transcript every time with parent_message_id=None REGENERATES
    the first bubble (UI shows 2/2, 3/3, 4/4) and looks like abuse — live
    accounts got suspended for this.

    First turn of a session: instructions + current user text (once).
    Later turns: only the new user/tool content after the last assistant.
    """
    last_user = ""
    for m in messages or []:
        if m.get("role") == "user":
            last_user = _msg_text(m)
    if not primed:
        body = _system_prompt(system, tools)
        if last_user:
            body += "\n\n---\nUSER:\n" + last_user
        return body
    chunks: List[str] = []
    for m in messages or []:
        role = m.get("role")
        if role == "system":
            continue
        if role == "assistant":
            chunks = []          # only keep what comes AFTER last assistant
            continue
        if role == "tool":
            chunks.append(f"TOOL RESULT ({m.get('name', 'tool')}): {_msg_text(m)[:4000]}")
        elif role == "user":
            chunks.append(_msg_text(m))
    text = "\n\n".join(c for c in chunks if str(c).strip())
    return text or last_user or "(continue)"


def _msg_text(m: dict) -> str:
    content = m.get("content") or ""
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content)


def extract_message_id(data: Any) -> Any:
    """Last message_id seen in a DeepSeek completion SSE/JSON body."""
    found: List[Any] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k in ("message_id", "msg_id"):
                if o.get(k) not in (None, ""):
                    found.append(o[k])
            p = o.get("p")
            if p in ("response/message_id", "message_id") and o.get("v") not in (None, ""):
                found.append(o["v"])
            resp = o.get("response")
            if isinstance(resp, dict) and resp.get("message_id") not in (None, ""):
                found.append(resp["message_id"])
            v = o.get("v")
            if isinstance(v, (dict, list)):
                walk(v)
            elif isinstance(o.get("data"), (dict, list)):
                walk(o["data"])
        elif isinstance(o, list):
            for x in o:
                walk(x)

    if isinstance(data, str):
        s = data.strip()
        if s.startswith("{") and "message_id" in s:
            try:
                walk(json.loads(s))
            except Exception:
                pass
        for line in data.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            js = line[5:].strip()
            if js == "[DONE]":
                break
            try:
                walk(json.loads(js))
            except Exception:
                continue
    else:
        walk(data)
    return found[-1] if found else None


def _serialize(messages: List[dict]) -> str:
    """Flatten OpenAI-style messages into a single text prompt for DeepSeek."""
    out: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        if role == "system":
            continue
        content = m.get("content") or ""
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        content = str(content)
        if role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                dsml = format_dsml_calls(tcs)
                if dsml:
                    out.append("ASSISTANT:\n" + dsml)
                else:
                    for tc in tcs:
                        fn = tc.get("function", {})
                        out.append(f"ASSISTANT TOOL CALL: {fn.get('name')} "
                                   f"{fn.get('arguments','')}")
            if content.strip() and not tcs:
                out.append(f"ASSISTANT: {content}")
        elif role == "tool":
            out.append(f"TOOL RESULT ({m.get('name','tool')}): {content}")
        elif role == "user":
            # tag prior user turns to distinguish from the current one
            out.append(f"USER: {content}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class DeepSeekProvider(BaseProvider):
    name = "deepseek"
    supports_tools = True          # via text-based tool calling
    supports_embeddings = False
    supports_moderation = False
    supports_ocr = False
    token_based = True             # auth via email+password -> bearer token

    def __init__(self, cfg: dict, keyring, notifier=None):
        super().__init__(cfg, keyring, notifier)
        self.mode = cfg.get("mode", DEFAULT_MODE)
        self.thinking = bool(cfg.get("thinking", True))
        self.search = bool(cfg.get("search", False))
        self.timeout = int(cfg.get("timeout", 180))
        self.account = DeepSeekAccount(Path(cfg.get("keys_dir", "./keys")), self.notify)
        self.pow = PoWSolver(Path(cfg.get("data_dir", "./.deepseek")), self.notify)
        self._session: Optional[str] = None
        self._parent_id: Optional[str] = None
        self._created_sessions: List[str] = []
        self._token: Optional[str] = None
        self._attach: Optional[Callable[[], str]] = None
        self._attached_files: List[tuple] = []
        self._stream_buf = False

    # ---- public controls (used by the CLI) ------------------------------
    def set_mode(self, mode: str) -> str:
        mode = (mode or "").lower()
        if mode == "instant":
            mode = "default"
        if mode not in MODES:
            raise ValueError("mode must be instant|expert|vision")
        self.mode = mode
        return mode

    def get_mode_label(self) -> str:
        return MODES[self.mode]["label"]

    def set_thinking(self, on: bool) -> None:
        self.thinking = bool(on)

    def set_search(self, on: bool) -> None:
        self.search = bool(on)

    def set_paste_callback(self, cb: Callable[[], str]) -> None:
        self._attach = cb

    def account_ok(self) -> bool:
        """True when we have credentials OR a token on disk (for showing in UI)."""
        return self.account.load_account() is not None or bool(self.account.load_cached_token())

    def has_token(self) -> bool:
        """True only when an actual token file exists (a completed login)."""
        return bool(self.account.load_cached_token())

    def reset_session(self) -> None:
        """Start a fresh DeepSeek chat thread on the next completion.

        Used by `/new` only. Follow-up goals in the same REPL MUST stay on
        this session with parent_message_id set — a new chat (or parent=None)
        regenerates the first bubble (UI 2/2, 3/3, 4/4) and gets accounts
        suspended.
        """
        self._session = None
        self._parent_id = None
        self._primed = False

    def current_session(self) -> Optional[str]:
        return self._session

    def created_sessions(self) -> List[str]:
        return list(self._created_sessions)

    def list_remote_sessions(self) -> List[dict]:
        """Sessions stored on the DeepSeek account (sidebar chats)."""
        token = self._token_or_login(interactive=False)
        try:
            r = requests.get(
                f"{_BASE}/chat_session/fetch_page",
                headers={**_HEADERS, "Authorization": f"Bearer {token}"},
                timeout=20)
            data = r.json() if r.status_code == 200 else {}
        except Exception as e:
            self.notify("warn", f"list sessions failed: {e}")
            return []
        biz = (data.get("data") or {}).get("biz_data") or data.get("data") or {}
        items = (biz.get("chat_sessions") or biz.get("items") or biz.get("list")
                 or biz.get("sessions") or [])
        if isinstance(items, dict):
            items = items.get("list") or items.get("items") or []
        out = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            sid = it.get("id") or it.get("chat_session_id") or ""
            title = it.get("title") or it.get("name") or ""
            out.append({"id": sid, "title": title, "raw": it})
        return out

    def delete_session(self, sid: str = "") -> bool:
        """Delete a chat session on the DeepSeek account (gone from sidebar too)."""
        sid = (sid or self._session or "").strip()
        if not sid:
            return False
        payloads = [
            {"chat_session_id": sid},
            {"id": sid},
            {"ids": [sid]},
        ]
        paths = ["/chat_session/delete", "/chat_session/delete_chat_session"]
        last: Any = None
        for path in paths:
            for payload in payloads:
                try:
                    data = self._request(path, payload)
                    ok = True
                    if isinstance(data, dict) and data.get("code") not in (None, 0, 200):
                        ok = False
                    if ok:
                        if self._session == sid:
                            self._session = None
                        if sid in self._created_sessions:
                            self._created_sessions.remove(sid)
                        self.notify("ok", f"deleted DeepSeek chat session {sid[:12]}")
                        return True
                    last = data
                except Exception as e:
                    last = e
                    continue
        self.notify("warn", f"could not delete session {sid[:12]}: {last}")
        return False

    def delete_all_created_sessions(self) -> int:
        n = 0
        for sid in list(self._created_sessions):
            if self.delete_session(sid):
                n += 1
        return n

    # ---- token ----------------------------------------------------------
    def _token_or_login(self, interactive: bool = True) -> str:
        if self._token:
            return self._token
        self._token = self.account.ensure_token(interactive=interactive,
                                                paste_callback=self._attach)
        return self._token

    # ---- low-level request ----------------------------------------------
    def _request(self, path: str, payload: dict, pow_path: str = "",
                 expect_json: bool = True) -> Any:
        """POST to DeepSeek using `requests` + the Android UA (WAF-safe).

        Returns parsed JSON for normal calls, or the raw SSE text for streams.
        """
        token = self._token_or_login()
        transient = (requests.exceptions.ConnectionError,
                     requests.exceptions.Timeout,
                     requests.exceptions.ChunkedEncodingError,
                     IncompleteRead, ConnectionResetError, TimeoutError, OSError)
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):            # up to 3 tries on transient errors
            try:
                headers = {**_HEADERS, "Authorization": f"Bearer {token}"}
                if pow_path:
                    headers["x-ds-pow-response"] = self.pow.solve(token, pow_path)
                resp = requests.post(
                    f"{_BASE}{path}", headers=headers, json=payload,
                    timeout=self.timeout, stream=not expect_json)
                if resp.status_code != 200:
                    if resp.status_code in (401, 403) and attempt == 1:
                        self.notify("warn", "DeepSeek auth failed — refreshing token…")
                        try:
                            token = self.account.refresh(
                                interactive=False, paste_callback=self._attach)
                            self._token = token
                            continue
                        except Exception:
                            pass
                    raise ProviderError(
                        f"DeepSeek HTTP {resp.status_code}: {resp.text[:200]}",
                        status=resp.status_code)
                if not expect_json:
                    return resp.text
                try:
                    data = resp.json()
                except Exception:
                    return resp.text
                if data.get("code") in (40003, 40004):       # INVALID_TOKEN / expired
                    if attempt == 1:
                        self.notify("warn", "DeepSeek token expired — regenerating…")
                        token = self.account.refresh(
                            interactive=False, paste_callback=self._attach)
                        self._token = token
                        continue
                return data
            except ProviderError:
                raise
            except transient as e:             # network hiccup / truncated SSE
                last_err = e
                if isinstance(e, IncompleteRead) or isinstance(
                        e, requests.exceptions.ChunkedEncodingError):
                    self.notify("warn", f"DeepSeek stream cut short ({attempt}/3) — retrying…")
                else:
                    self.notify("warn", f"DeepSeek network error ({attempt}/3): {e}")
                time.sleep(min(3, 0.5 * attempt))
                continue
        raise ProviderError(f"DeepSeek request failed after retries: {last_err}")

    # ---- completion -----------------------------------------------------
    def chat(self, model: str, messages: List[dict], tools: Optional[List[dict]] = None,
             **params: Any) -> ChatResult:
        # find system content
        system = ""
        for m in messages:
            if m.get("role") == "system":
                system = str(m.get("content") or "")
        prompt = turn_prompt(self._primed, system, tools, messages)
        if not self._session:
            try:
                sess = self._request("/chat_session/create", {"character_id": None})
                bd = (sess.get("data", {}) or {}).get("biz_data", {}) or {}
                # session id is under `id` (older clients used `chat_session.id`)
                self._session = bd.get("id") or (bd.get("chat_session") or {}).get("id")
                if self._session and self._session not in self._created_sessions:
                    self._created_sessions.append(self._session)
                self._parent_id = None
                self._primed = False
                prompt = turn_prompt(False, system, tools, messages)
            except Exception:
                self._session = None

        rules = MODES[self.mode]
        thinking = self.thinking
        search = self.search
        if not rules["supports_search"] and search:
            self.notify("warn", f"web search blocked in {rules['label']} mode — disabled")
            search = False
        file_ids = [f[0] for f in self._attached_files if rules["supports_files"]]
        if self._attached_files and not rules["supports_files"]:
            self.notify("warn", f"attached files blocked in {rules['label']} mode — detached")
            self._attached_files = []
            file_ids = []

        payload = {
            "chat_session_id": self._session,
            "parent_message_id": self._parent_id,
            "prompt": prompt,
            "stream": True,
            "ref_file_ids": file_ids,
            "thinking_enabled": bool(thinking),
            "search_enabled": bool(search),
            "model_type": rules["model_type"],
        }
        data = self._request("/chat/completion", payload,
                             pow_path="/api/v0/chat/completion", expect_json=False)
        mute = _mute_error(data)
        if mute:
            raise ProviderError(mute, retryable=False)
        text, think = self._collect(data)
        self._primed = True
        mid = extract_message_id(data)
        if mid is not None:
            self._parent_id = mid
        elif self._parent_id is None:
            self._parent_id = 1
        else:
            try:
                self._parent_id = int(self._parent_id) + 1
            except Exception:
                pass
        # V4 expert/thinking often emits DSML inside the THINK stream, not RESPONSE.
        blob = text or ""
        if think and (looks_like_dsml(think) or "TOOL_CALL" in think
                      or "<invoke" in think or "<tool_call" in think.lower()):
            blob = (text or "") + "\n" + think
        calls = _extract_tool_calls(blob)
        content = _strip_tool_calls(text).strip()
        if calls and not content:
            content = ""
        self._attached_files = []           # files consumed on this message
        if think.strip() and not calls:
            self.notify("info", f"[deepseek {rules['label']} · thinking] {think[:200]}")
        elif think.strip() and calls:
            self.notify("info", f"[deepseek {rules['label']} · thinking] {think[:160]}")
        return ChatResult(
            content=content,
            tool_calls=calls,
            model=f"deepseek-{self.mode}",
            provider=self.name,
            key_label="deepseek",
            prompt_tokens=0,
            completion_tokens=0,
            finish_reason="stop",
            latency=0.0,
            raw={"text": text, "thinking": think},
        )

    def _collect(self, data: Any) -> tuple:
        """Extract RESPONSE + THINK text from the completion SSE body."""
        text: List[str] = []
        think: List[str] = []
        state = {"frag": "RESPONSE"}   # current fragment type for bare 'v' continuations
        if isinstance(data, dict):
            # server sometimes buffers events into a JSON wrapper
            self._collect_event(data, text, think, state)
            return "".join(text), "".join(think)
        if isinstance(data, str):
            for line in data.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                js = line[5:].strip()
                if js == "[DONE]":
                    break
                try:
                    d = json.loads(js)
                except Exception:
                    continue
                self._collect_event(d, text, think, state)
        return "".join(text), "".join(think)

    @staticmethod
    def _collect_event(d: dict, text: List[str], think: List[str], state: dict) -> None:
        # "v" is either a fragment-bundle dict or a bare string continuation
        v = d.get("v")
        if isinstance(v, dict) and "response" in v:
            resp = v["response"]
            frags = resp.get("fragments") or []
            if frags:
                f = frags[0]
                state["frag"] = f.get("type", "RESPONSE")
                (think if state["frag"] == "THINK" else text).append(f.get("content", ""))
            if isinstance(resp.get("content"), str) and resp["content"]:
                text.append(resp["content"])
        elif isinstance(v, str):
            (think if state["frag"] == "THINK" else text).append(v)
        elif d.get("o") == "APPEND":
            val = d.get("v", "")
            path = d.get("p", "")
            if isinstance(val, list) and "fragments" in path:
                if val:
                    state["frag"] = val[0].get("type", "RESPONSE")
                    (think if state["frag"] == "THINK" else text).append(
                        val[0].get("content", ""))
            elif path == "response/fragments/-1/content":
                (think if state["frag"] == "THINK" else text).append(
                    val if isinstance(val, str) else "")

    def embed(self, model: str, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError("DeepSeek has no embeddings endpoint (RAG uses keyword fallback)")
