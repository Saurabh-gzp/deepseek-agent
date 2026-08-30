"""DeepSeek V4 native tool-calling: DSML (DeepSeek Markup Language).

The web model is trained to emit:

    <|DSML|tool_calls>
      <|DSML|invoke name="write_file">
        <|DSML|parameter name="path"><![CDATA[index.html]]></|DSML|parameter>
        <|DSML|parameter name="content"><![CDATA[...]]></|DSML|parameter>
      </|DSML|invoke>
    </|DSML|tool_calls>

Live V4 also uses the fullwidth pipe U+FF5C (｜) and an attribute form:

    <｜DSML｜parameter name="path" string="true">index.html

The previous harness only looked for `TOOL_CALL: {json}` / Claude `<invoke>`,
so native DSML was treated as plain text. The model then "finished" and
fabricated "I hosted it at localhost:8080".
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

FW_PIPE = "\uFF5C"  # ｜
LOWER_BLOCK = "\u2581"  # ▁  (begin▁of▁sentence tokens)


def normalize_dsml(text: str) -> str:
    if not text:
        return ""
    t = text.replace(FW_PIPE, "|").replace(LOWER_BLOCK, "_")
    # Live V4 sometimes drops the opening pipe: <DSML|invoke  /  </DSML|parameter>
    t = re.sub(r"</\s*\|?\s*DSML\s*\|?\s*", "</|DSML|", t, flags=re.I)
    t = re.sub(r"<\s*\|?\s*DSML\s*\|?\s*", "<|DSML|", t, flags=re.I)
    t = re.sub(r"<dsml-", "<|DSML|", t, flags=re.I)
    t = re.sub(r"</dsml-", "</|DSML|", t, flags=re.I)
    return t


def _cdata(inner: str) -> str:
    inner = inner.strip()
    if inner.startswith("<![CDATA[") and inner.endswith("]]>"):
        inner = inner[9:-3]
        inner = inner.replace("]]]]><![CDATA[>", "]]>")
    return inner


def _auto_type(val: str) -> Any:
    s = val.strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "none"):
        return None
    try:
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        return float(s) if re.fullmatch(r"-?\d+\.\d+", s) else s
    except ValueError:
        return s


def _parse_params(inner: str) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    # Closed tags first.
    param_re = re.compile(
        r'<\|DSML\|parameter\s+name="([^"]+)"([^>]*)>(.*?)</\|DSML\|parameter>',
        re.DOTALL | re.I,
    )
    used = False
    for m in param_re.finditer(inner):
        used = True
        key = m.group(1).strip()
        raw = _cdata(m.group(3))
        try:
            val: Any = json.loads(raw)
        except Exception:
            val = _auto_type(raw)
        args[key] = val
    if used:
        return args
    # Attribute / unclosed form:
    #   <|DSML|parameter name="path" string="true">index.html
    #   (value runs until the next DSML tag)
    loose = re.compile(
        r'<\|DSML\|parameter\s+name="([^"]+)"[^>]*>(.*?)(?=<\|DSML\||</\|DSML\||\Z)',
        re.DOTALL | re.I,
    )
    for m in loose.finditer(inner):
        key = m.group(1).strip()
        raw = _cdata(m.group(2))
        try:
            val = json.loads(raw)
        except Exception:
            val = _auto_type(raw)
        args[key] = val
    # Claude-style <parameter name="x">val</parameter>
    if not args:
        for m in re.finditer(
            r'<parameter\s+name="([^"]+)">(.*?)</parameter>', inner, re.DOTALL | re.I
        ):
            raw = _cdata(m.group(2))
            try:
                val = json.loads(raw)
            except Exception:
                val = _auto_type(raw)
            args[m.group(1).strip()] = val
    return args


def _call(name: str, args: Dict[str, Any]) -> dict:
    return {
        "id": f"call_{int(time.time() * 1000) % 10_000_000}_{name[:12]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def extract_dsml_calls(text: str) -> List[dict]:
    """Return OpenAI-style tool_calls parsed from DSML (any pipe variant)."""
    if not text:
        return []
    norm = normalize_dsml(text)
    if "DSML" not in norm.upper() and "<invoke" not in norm.lower() and "<tool_call" not in norm.lower():
        return []
    calls: List[dict] = []
    invoke_re = re.compile(
        r'<\|DSML\|invoke\s+name="([^"]+)"\s*/?>'
        r'(?:(?P<body>.*?)</\|DSML\|invoke>)?',
        re.DOTALL | re.I,
    )
    # Prefer closed invokes; fall back to invoke ... next invoke / tool_calls end.
    closed = re.compile(
        r'<\|DSML\|invoke\s+name="([^"]+)"\s*>(.*?)</\|DSML\|invoke>',
        re.DOTALL | re.I,
    )
    found = list(closed.finditer(norm))
    if not found:
        found = list(re.finditer(
            r'<\|DSML\|invoke\s+name="([^"]+)"\s*>(.*?)(?=<\|DSML\|invoke|</\|DSML\|tool_calls>|\Z)',
            norm, re.DOTALL | re.I,
        ))
    for m in found:
        name = (m.group(1) or "").strip()
        body = m.group(2) or ""
        if name:
            calls.append(_call(name, _parse_params(body)))
    if calls:
        return calls
    # Bare <invoke name="x"> (no DSML prefix) — already handled upstream too.
    for m in re.finditer(
        r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>', norm, re.DOTALL | re.I
    ):
        name = m.group(1).strip()
        if name:
            calls.append(_call(name, _parse_params(m.group(2))))
    return calls


def looks_like_dsml(text: str) -> bool:
    if not text:
        return False
    t = text.replace(FW_PIPE, "|")
    return bool(re.search(
        r"<\|?\s*DSML\s*\|?\s*(tool_calls|invoke|parameter|\w*)"
        r"|</\|?\s*DSML",
        t, re.I))


def strip_dsml(text: str) -> str:
    """Remove DSML / tool-call markup so the leftover is the prose answer."""
    if not text:
        return ""
    out = normalize_dsml(text)
    out = re.sub(r"<\|DSML\|tool_calls>.*?</\|DSML\|tool_calls>", "", out,
                 flags=re.DOTALL | re.I)
    out = re.sub(r"<\|DSML\|invoke\b.*?(?:</\|DSML\|invoke>|(?=<\|DSML\|invoke)|(?=<\|DSML\|tool_calls)|\Z)",
                 "", out, flags=re.DOTALL | re.I)
    out = re.sub(r"<\|DSML\|parameter\b.*?(?:</\|DSML\|parameter>|\Z)", "",
                 out, flags=re.DOTALL | re.I)
    out = re.sub(r"<tool_calls?>.*?</tool_calls?>", "", out, flags=re.DOTALL | re.I)
    out = re.sub(r"<invoke\b.*?</invoke>", "", out, flags=re.DOTALL | re.I)
    out = re.sub(r"<!\[CDATA\[.*?\]\]>", "", out, flags=re.DOTALL)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def format_dsml_calls(tool_calls: List[dict]) -> str:
    """Replay executed calls back to the model in its native format."""
    if not tool_calls:
        return ""
    blocks = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or ""
        if not name:
            continue
        raw = fn.get("arguments") or tc.get("arguments") or "{}"
        if isinstance(raw, str):
            try:
                args = json.loads(raw)
            except Exception:
                args = {"_raw": raw}
        else:
            args = raw or {}
        params = []
        for k, v in (args.items() if isinstance(args, dict) else []):
            if isinstance(v, (dict, list)):
                body = json.dumps(v, ensure_ascii=False)
            else:
                body = str(v)
            if "]]>" in body:
                body = body.replace("]]>", "]]]]><![CDATA[>")
            params.append(
                f'    <|DSML|parameter name="{k}"><![CDATA[{body}]]></|DSML|parameter>'
            )
        inner = ("\n" + "\n".join(params) + "\n  ") if params else ""
        blocks.append(f'  <|DSML|invoke name="{name}">{inner}</|DSML|invoke>')
    if not blocks:
        return ""
    return "<|DSML|tool_calls>\n" + "\n".join(blocks) + "\n</|DSML|tool_calls>"


def build_dsml_tool_prompt(tools: List[dict]) -> str:
    """Short native-format instruction + per-tool schema."""
    if not tools:
        return ""
    lines = []
    for spec in tools:
        fn = spec.get("function", spec)
        name = fn.get("name", "?")
        desc = (fn.get("description") or "").strip()
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        req = set(params.get("required") or [])
        arg_bits = []
        for pname, pmeta in props.items():
            mark = " REQUIRED" if pname in req else ""
            ptype = (pmeta or {}).get("type", "any")
            arg_bits.append(f"{pname}:{ptype}{mark}")
        lines.append(f"- {name}: {desc[:180]}\n    args: {', '.join(arg_bits) or '(none)'}")
    catalog = "\n".join(lines)
    return (
        "You have tools. When you need one, emit ONLY a DSML block "
        "(no markdown fences, no prose around it):\n\n"
        "<|DSML|tool_calls>\n"
        "  <|DSML|invoke name=\"TOOL_NAME\">\n"
        "    <|DSML|parameter name=\"ARG\"><![CDATA[VALUE]]></|DSML|parameter>\n"
        "  </|DSML|invoke>\n"
        "</|DSML|tool_calls>\n\n"
        "Also accepted: TOOL_CALL: {\"name\":\"TOOL_NAME\",\"arguments\":{...}}\n"
        "Fill every REQUIRED argument with a real value. After the tool result "
        "arrives, continue. When the task is actually done, reply in plain text "
        "with NO tool block.\n"
        "NEVER claim a file exists, a command ran, or a server is live unless a "
        "TOOL RESULT in this conversation proved it. Fabricating results is a failure.\n\n"
        "Available tools:\n" + catalog
    )
