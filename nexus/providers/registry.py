"""Provider registry — plug-and-play provider system.

Naya provider add karne ke liye:
    1. nexus/providers/myprovider.py me BaseProvider subclass banao
    2. PROVIDER_TYPES dict me register karo
    3. config.yaml -> providers.myprovider: {enabled: true, type: myprovider, ...}
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

from .base import BaseProvider
from .keyring import KeyRing
from .mistral import MistralProvider
from .openai_compat import OpenAICompatibleProvider

PROVIDER_TYPES: Dict[str, type] = {
    "mistral": MistralProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "openai": OpenAICompatibleProvider,
}


def register_provider(type_name: str, cls: type) -> None:
    PROVIDER_TYPES[type_name] = cls


class ProviderRegistry:
    def __init__(self, config, notifier: Optional[Callable[[str, str], None]] = None):
        self.config = config
        self.notify = notifier or (lambda level, msg: None)
        self.providers: Dict[str, BaseProvider] = {}
        self.keyrings: Dict[str, KeyRing] = {}
        self._build()

    def _build(self) -> None:
        from ..core.keymanager import KeyManager
        km = KeyManager(self.config)
        try:
            moved = km.migrate_legacy(self.config.data_dir / "keys.json")
            if moved:
                self.notify("ok", f"migrated {moved} key(s) to keys/ folder")
        except Exception:
            pass
        file_keys = km.all()
        keyfile = self.config.data_dir / "keys.json"
        for pname, pcfg in (self.config.get("providers", {}) or {}).items():
            if pname == "default" or not isinstance(pcfg, dict):
                continue
            if not pcfg.get("enabled"):
                continue
            keys = KeyRing.discover(pname, pcfg.get("env_keys", []), keyfile)
            for fk in file_keys.get(pname, []):        # keys/<provider>.json wali keys
                if fk not in keys:
                    keys.append(fk)
            if not keys and not pcfg.get("api_key"):
                self.notify("warn", f"Provider '{pname}' enabled but no API keys found — skipped")
                continue
            ring = KeyRing(
                pname, keys,
                cooldown=int(self.config.get("failover.cooldown_seconds", 60)),
                hard_cooldown=int(self.config.get("failover.hard_fail_cooldown", 600)),
                notifier=self.notify if self.config.get("failover.notify_user", True) else None,
            )
            cls = PROVIDER_TYPES.get(pcfg.get("type", pname))
            if cls is None:
                self.notify("error", f"Unknown provider type '{pcfg.get('type')}' for {pname}")
                continue
            merged = dict(pcfg)
            merged["max_key_rotations_per_call"] = self.config.get(
                "failover.max_key_rotations_per_call", 6)
            try:
                inst = cls(merged, ring, self.notify)
                inst.name = pname
                self.providers[pname] = inst
                self.keyrings[pname] = ring
            except Exception as e:  # noqa: BLE001
                self.notify("error", f"Failed to init provider {pname}: {e}")

    # ------------------------------------------------------------------
    def ensure_provider(self, pname: str, keys: Optional[List[str]] = None):
        """Wizard//key runtime-add: provider live banao (0-keys skip utha lo).

        Agar provider pehle se hai → sirf naye keys ring me add hote hain.
        Returns keyring ya None.
        """
        from .keyring import KeyRing
        pcfg = (self.config.get("providers", {}) or {}).get(pname)
        if not isinstance(pcfg, dict):
            self.notify("error", f"unknown provider '{pname}'")
            return None
        ring = self.keyrings.get(pname)
        if ring is None:
            ring = KeyRing(
                pname, keys or [],
                cooldown=int(self.config.get("failover.cooldown_seconds", 60)),
                hard_cooldown=int(self.config.get("failover.hard_fail_cooldown", 600)),
                notifier=self.notify if self.config.get("failover.notify_user", True) else None)
            self.keyrings[pname] = ring
        elif keys:
            for k in keys:
                ring.add_key(k)
        if pname in self.providers:
            return ring
        cls = PROVIDER_TYPES.get(pcfg.get("type", pname))
        if cls is None:
            self.notify("error", f"Unknown provider type '{pcfg.get('type')}' for {pname}")
            return ring
        merged = dict(pcfg)
        merged["max_key_rotations_per_call"] = self.config.get(
            "failover.max_key_rotations_per_call", 6)
        try:
            inst = cls(merged, ring, self.notify)
            inst.name = pname
            self.providers[pname] = inst
        except Exception as e:  # noqa: BLE001
            self.notify("error", f"Failed to init provider {pname}: {e}")
        return ring

    @property
    def default_name(self) -> str:
        d = self.config.get("providers.default", "mistral")
        if d in self.providers:
            return d
        return next(iter(self.providers), "")

    def get(self, name: Optional[str] = None) -> BaseProvider:
        name = name or self.default_name
        if name not in self.providers:
            raise RuntimeError(
                f"Provider '{name}' unavailable. Configured: {list(self.providers)}. "
                "Run `nexus keys add` or set MISTRAL_API_KEY."
            )
        return self.providers[name]

    def order(self) -> List[str]:
        """Default provider first, then others (cross-provider failover)."""
        d = self.default_name
        return ([d] if d else []) + [p for p in self.providers if p != d]

    def status(self) -> Dict[str, list]:
        return {n: r.status() for n, r in self.keyrings.items()}

    def total_keys(self) -> int:
        return sum(len(r) for r in self.keyrings.values())
