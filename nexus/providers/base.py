"""Provider abstraction layer.

To add a new provider, just extend `BaseProvider` and
register it in `nexus/providers/registry.py`. The rest of the system stays unchanged.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


@dataclass
class ChatMessage:
    role: str
    content: Any = ""
    tool_calls: Optional[List[dict]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_api(self) -> dict:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[dict] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    key_label: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    latency: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ProviderError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, retryable: bool = True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class BaseProvider(abc.ABC):
    """Every provider must implement chat(); embed/moderate/ocr are optional."""

    name: str = "base"
    supports_tools: bool = True
    supports_embeddings: bool = False
    supports_moderation: bool = False
    supports_ocr: bool = False
    token_based: bool = False   # True => manages its own auth (e.g. DeepSeek login)

    def __init__(self, cfg: dict, keyring, notifier=None):
        self.cfg = cfg
        self.keyring = keyring
        self.notify = notifier or (lambda level, msg: None)

    @abc.abstractmethod
    def chat(
        self,
        model: str,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        **params: Any,
    ) -> ChatResult:
        ...

    def stream(self, model: str, messages: List[dict], **params) -> Iterator[str]:
        res = self.chat(model, messages, **params)
        yield res.content

    def embed(self, model: str, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError(f"{self.name} has no embeddings")

    def moderate(self, model: str, texts: List[str]) -> List[dict]:
        raise NotImplementedError(f"{self.name} has no moderation")

    def ocr(self, model: str, document: dict) -> dict:
        raise NotImplementedError(f"{self.name} has no OCR")
