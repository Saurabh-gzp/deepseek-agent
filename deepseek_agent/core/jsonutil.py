"""Robust JSON extraction from LLM output.

LLMs wrap JSON in prose, markdown fences, or emit trailing commas / single quotes.
This module recovers the intended object instead of failing the whole task.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def _balanced_objects(text: str) -> List[str]:
    """Yield every top-level balanced {...} block, string-aware."""
    out: List[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(text[start:i + 1])
    return out


def _repair(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.M)   # fences
    s = re.sub(r",(\s*[}\]])", r"\1", s)                          # trailing commas
    s = re.sub(r"(?<![\\\w])'([^'\n]*)'(\s*:)", r'"\1"\2', s)     # 'key':
    s = s.replace("\u201c", '"').replace("\u201d", '"')           # smart quotes
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNone\b", "null", s)
    return s


def extract_json(text: str, require_keys: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Best-effort parse of a JSON object out of arbitrary LLM text."""
    if not text:
        return None
    candidates: List[str] = []
    cleaned = _repair(text)
    candidates.append(cleaned)
    candidates.extend(_balanced_objects(cleaned))
    candidates.extend(_balanced_objects(text))

    best: Optional[Dict[str, Any]] = None
    for cand in candidates:
        for attempt in (cand, _repair(cand)):
            try:
                obj = json.loads(attempt)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if require_keys:
                if all(k in obj for k in require_keys):
                    return obj
                if best is None:
                    best = obj
                continue
            return obj
    return best


def extract_field(text: str, field: str) -> Optional[str]:
    """Grab a single "field": "value" even from broken JSON."""
    m = re.search(rf'"{re.escape(field)}"\s*:\s*"([^"]*)"', text or "")
    if m:
        return m.group(1)
    m = re.search(rf'"{re.escape(field)}"\s*:\s*([\d.]+|true|false|null)', text or "")
    return m.group(1) if m else None
