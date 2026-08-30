"""KeyManager — API keys ka persistent store `keys/` folder me.

Design (user ke idea ka robust version):
    keys/
      mistral.json     {"provider": "mistral", "keys": ["...", "..."]}
      openai.json      (jab enable ho)

* Per-provider file, plain JSON, chmod 600 (folder 700)
* At startup the registry merges these files with env keys (dedup)
* live add/delete via the /key menu — synced into KeyRing without a restart
* Purana .deepseek/keys.json automatic migrate ho jaata hai
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "****"


class KeyManager:
    def __init__(self, config) -> None:
        self.dir: Path = config._abs("keys.dir", "./keys")

    # ------------------------------------------------------------------
    def _file(self, provider: str) -> Path:
        return self.dir / f"{provider}.json"

    def _read(self, provider: str) -> List[str]:
        f = self._file(provider)
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            keys = data.get("keys", [])
            return [k for k in keys if isinstance(k, str) and k.strip()]
        except Exception:
            return []

    def all(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        if not self.dir.exists():
            return out
        for f in sorted(self.dir.glob("*.json")):
            keys = self._read(f.stem)
            if keys:
                out[f.stem] = keys
        return out

    def load(self, provider: str) -> List[str]:
        return self._read(provider)

    # ------------------------------------------------------------------
    def add(self, provider: str, key: str) -> bool:
        key = (key or "").strip()
        if not key:
            return False
        keys = self._read(provider)
        if key in keys:                      # dedup
            return False
        keys.append(key)
        return self._write(provider, keys)

    def remove_at(self, provider: str, index: int) -> Optional[str]:
        """1-based index delete; returns removed key ya None."""
        keys = self._read(provider)
        if 1 <= index <= len(keys):
            removed = keys.pop(index - 1)
            self._write(provider, keys)
            return removed
        return None

    def remove_value(self, provider: str, key: str) -> bool:
        keys = self._read(provider)
        if key in keys:
            keys.remove(key)
            return self._write(provider, keys)
        return False

    def _write(self, provider: str, keys: List[str]) -> bool:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.dir, 0o700)
            except Exception:
                pass
            f = self._file(provider)
            f.write_text(json.dumps(
                {"provider": provider,
                 "keys": keys}, indent=2), encoding="utf-8")
            try:
                os.chmod(f, 0o600)
            except Exception:
                pass
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    def migrate_legacy(self, legacy_file: Path) -> int:
        """.deepseek/keys.json (purana format) -> keys/ folder. Returns moved count."""
        if not legacy_file.exists() or self.dir.exists() and self.all():
            return 0
        try:
            data = json.loads(legacy_file.read_text(encoding="utf-8"))
        except Exception:
            return 0
        moved = 0
        for prov, entries in (data or {}).items():
            if not isinstance(entries, list):
                continue
            for k in entries:
                if isinstance(k, str) and self.add(prov, k):
                    moved += 1
        if moved:
            try:
                legacy_file.unlink()          # keys now live in keys/
            except Exception:
                pass
        return moved


def unified_keys(file_keys: List[str], ring) -> List[dict]:
    """keys/ + .env — all keys in one numbered list (dedup, with a source tag).

    Returns [{"n":1, "value":"sk-..", "masked":"sk-…", "src":"keys/"|".env"}, ...]
    """
    out: List[dict] = []
    seen = set()
    for k in file_keys:
        if k not in seen:
            seen.add(k)
            out.append({"n": len(out) + 1, "value": k, "masked": mask(k), "src": "keys/"})
    if ring is not None:
        for k in getattr(ring, "keys", []):
            v = k.value
            if v not in seen:
                seen.add(v)
                out.append({"n": len(out) + 1, "value": v, "masked": k.masked, "src": ".env"})
    return out
