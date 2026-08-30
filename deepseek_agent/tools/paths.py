"""Workspace path-boundary checks (no string-prefix sandbox)."""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


def in_workspace(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------
# v1.10.4 §10 TOOL ARGUMENT INTELLIGENCE
# ---------------------------------------------------------------------
# Live bug: the agent called list_dir("workspace") while the ACTIVE workspace
# already IS .../deepseek-agent/workspace, got "Not found: workspace", burned a
# critic round, and recovered on the second try. A wrong path argument is a
# *normalisation* problem, not a decision problem — fix it in the harness.
_PATH_KEYS = ("path", "directory", "cwd", "src", "dst", "from_path", "to_path")
_REDUNDANT_PREFIX = ("workspace/", "./workspace/", "workspace", "./", ".\\")


def normalize_path_arg(value: str, root: Path) -> Tuple[str, str]:
    """Return (corrected_value, note). Never invents a path that doesn't exist."""
    raw = (value or "").strip().strip('"').strip("'")
    if not raw:
        return raw, ""
    p = Path(raw)
    if p.is_absolute():
        return raw, ""
    direct = (root / raw)
    if direct.exists():
        return raw, ""
    # shape 1: 'workspace/...' while root already IS the workspace
    low = raw.lower()
    for pref in _REDUNDANT_PREFIX:
        if low == pref.rstrip("/") or low.startswith(pref):
            stripped = raw[len(pref):].lstrip("/") or "."
            if (root / stripped).exists():
                return stripped, (f"note: '{raw}' was resolved relative to the "
                                  f"workspace root — corrected to '{stripped}' "
                                  f"(workspace = {root})")
    # shape 2: relative path that exists under exactly one subdirectory
    name = p.name
    if name and not p.parent.parts:
        try:
            hits = [q for q in root.rglob(name) if q.is_file() or q.is_dir()]
        except OSError:
            hits = []
        if len(hits) == 1:
            rel = str(hits[0].relative_to(root))
            return rel, (f"note: '{raw}' not found at that path; used the unique "
                         f"match '{rel}'")
    return raw, ""


def normalize_tool_args(name: str, args: dict, root: Path) -> Tuple[dict, List[str]]:
    """Normalise path-ish arguments of a tool call. Returns (args, notes)."""
    if not isinstance(args, dict):
        return args, []
    out, notes = dict(args), []
    for key in _PATH_KEYS:
        v = out.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        fixed, note = normalize_path_arg(v, root)
        if note:
            out[key] = fixed
            notes.append(f"{key}: {note}")
    return out, notes
