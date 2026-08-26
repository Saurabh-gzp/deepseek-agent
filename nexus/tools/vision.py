"""Read an image (workspace or phone storage) and describe it with a vision model."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Callable, Optional

from .base import Risk, ToolRegistry, ToolResult

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}
MAX_BYTES = 6 * 1024 * 1024

# Read-only: the user already pointed at these when they asked "kya dikh rha".
PHONE_ROOTS = (
    "/storage/emulated/0",
    "/sdcard",
    "/storage/self/primary",
)


def allowed_image_path(path: Path) -> bool:
    s = str(path)
    if any(s == r or s.startswith(r + "/") for r in PHONE_ROOTS):
        return True
    home = Path.home()
    for extra in (home / "storage", home / "downloads", home / "Download"):
        try:
            path.resolve().relative_to(extra.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def resolve_image(raw: str, workspace: Path) -> Path:
    s = (raw or "").strip().strip("\"'").replace("\n", "").replace("\r", "")
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        p = (workspace / p).resolve()
    else:
        p = p.resolve()
    return p


class VisionTools:
    def __init__(self, workspace: Path, ask: Callable[..., str]):
        self.root = Path(workspace).resolve()
        self.ask = ask  # llm.ask(role, prompt, ...) or chat wrapper

    def see_image(self, path: str = "", question: str = "") -> ToolResult:
        if not path:
            return ToolResult(False, error="see_image needs path=")
        try:
            p = resolve_image(path, self.root)
        except OSError as e:
            return ToolResult(False, error=f"bad path: {e}")
        try:
            in_ws = p.resolve().relative_to(self.root)
        except (ValueError, OSError):
            in_ws = None
        if in_ws is None and not allowed_image_path(p):
            return ToolResult(False, error=(
                f"Image path not allowed (workspace or phone storage only): {p}"))
        if not p.exists() or not p.is_file():
            return ToolResult(False, error=f"Image not found: {p}")
        ext = p.suffix.lower()
        if ext not in IMAGE_EXT:
            return ToolResult(False, error=f"Not an image: {ext or p.name}")
        size = p.stat().st_size
        if size > MAX_BYTES:
            return ToolResult(False, error=f"Image too large ({size // 1024} KB, max {MAX_BYTES // 1024} KB)")
        mime = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            ".heic": "image/heic", ".heif": "image/heif",
        }.get(ext, "image/png")
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        q = (question or "").strip() or (
            "Describe this image in detail: what is visible, text, people, UI, objects. "
            "Be concrete. If you cannot see it, say so."
        )
        content = [
            {"type": "text", "text": q},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
        try:
            from ..llm.client import ChatResult  # type: ignore
        except Exception:
            ChatResult = None  # noqa: N806
        try:
            # Prefer chat() so multimodal content is passed through.
            llm = getattr(self, "_llm", None)
            if llm is not None and hasattr(llm, "chat"):
                res = llm.chat("vision", [{"role": "user", "content": content}])
                text = (res.content or "").strip()
                model = getattr(res, "model", "vision")
            else:
                text = (self.ask("vision", q) or "").strip()
                model = "vision"
            if not text:
                return ToolResult(False, error="Vision model returned empty description")
            return ToolResult(True, output=(
                f"IMAGE {p.name} ({size} bytes, {mime}) via {model}\n\n{text}"))
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=f"Vision failed: {e}")

    def register(self, reg: ToolRegistry, llm=None) -> None:
        self._llm = llm
        S = {"type": "string"}
        reg.add(
            "see_image",
            "LOOK at an image file and describe what is actually in it (vision). "
            "Use this whenever the user points at a .png/.jpg/.webp (phone path "
            "/storage/emulated/0/... or workspace). Do NOT only ls the folder — "
            "you must SEE the pixels. path= full file path, question= optional focus.",
            {"type": "object", "properties": {"path": S, "question": S},
             "required": ["path"]},
            self.see_image, Risk.READ_ONLY,
            agents=["supervisor", "worker", "researcher", "coder", "solo"],
        )
