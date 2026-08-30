"""Configuration loader for DeepSeek-Agent.

Priority:  CLI args  >  env vars  >  config/config.yaml  >  defaults
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


PKG_ROOT = Path(__file__).resolve().parent.parent.parent  # deepseek-agent/
DEFAULT_CONFIG = PKG_ROOT / "config" / "config.yaml"


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    raw: Dict[str, Any] = field(default_factory=dict)
    path: Path = DEFAULT_CONFIG
    root: Path = PKG_ROOT

    # ---------- loading ----------
    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "Config":
        p = Path(path) if path else DEFAULT_CONFIG
        data: Dict[str, Any] = {}
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if yaml is not None:
                data = yaml.safe_load(text) or {}
            else:  # very small fallback: json config
                data = json.loads(text)
        # local override file (git-ignored)
        local = p.parent / "config.local.yaml"
        if local.exists() and yaml is not None:
            data = _deep_merge(data, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
        cfg = cls(raw=data, path=p, root=p.parent.parent)
        cfg._apply_env_overrides()
        return cfg

    def _apply_env_overrides(self) -> None:
        # DEEPSEEK_APPROVAL_MODE, DEEPSEEK_WORKSPACE, DEEPSEEK_THEME ...
        if v := os.getenv("DEEPSEEK_WORKSPACE"):
            self.set("app.workspace", v)
        if v := os.getenv("DEEPSEEK_APPROVAL_MODE"):
            self.set("safety.approval_mode", v)
        if v := os.getenv("DEEPSEEK_THEME"):
            self.set("app.theme", v)
        if v := os.getenv("DEEPSEEK_PROVIDER"):
            self.set("providers.default", v)

    # ---------- access ----------
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self.raw
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value

    def save(self) -> None:
        if yaml is None:
            self.path.write_text(json.dumps(self.raw, indent=2), encoding="utf-8")
        else:
            self.path.write_text(yaml.safe_dump(self.raw, sort_keys=False, allow_unicode=True),
                                 encoding="utf-8")

    # ---------- resolved paths ----------
    def _abs(self, dotted: str, default: str) -> Path:
        raw = str(self.get(dotted, default))
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (self.root / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def workspace(self) -> Path:
        return self._abs("app.workspace", "./workspace")

    @property
    def data_dir(self) -> Path:
        return self._abs("app.data_dir", "./.deepseek")

    @property
    def skills_dir(self) -> Path:
        return self._abs("skills.dir", "./skills")

    @property
    def vector_dir(self) -> Path:
        raw = str(self.get("rag.index_dir", "./.deepseek/vectors"))
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (self.root / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def memory_db(self) -> Path:
        raw = str(self.get("memory.db_path", "./.deepseek/memory.db"))
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (self.root / p).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ---------- model roles ----------
    def model_for(self, role: str) -> str:
        return self.get(f"models.{role}.model", self.get("models.worker.model", "deepseek-chat"))

    def fallbacks_for(self, role: str) -> List[str]:
        return list(self.get(f"models.{role}.fallback", []) or [])

    def model_chain(self, role: str) -> List[str]:
        chain = [self.model_for(role)] + self.fallbacks_for(role)
        seen, out = set(), []
        for m in chain:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    def gen_params(self, role: str) -> Dict[str, Any]:
        node = self.get(f"models.{role}", {}) or {}
        out = {}
        if "temperature" in node:
            out["temperature"] = node["temperature"]
        if "max_tokens" in node:
            out["max_tokens"] = node["max_tokens"]
        return out

    def rate_limit(self, model: str) -> float:
        return float(self.get(f"rate_limits.{model}", self.get("rate_limits.default", 2.0)))


_active: Optional[Config] = None


def get_config(path: Optional[str | Path] = None, reload: bool = False) -> Config:
    global _active
    if _active is None or reload:
        _active = Config.load(path)
    return _active
