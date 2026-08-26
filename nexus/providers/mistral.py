"""Mistral provider — pure-stdlib HTTP (Termux friendly, koi heavy SDK nahi).

Har call automatically:
  * KeyRing se healthy key uthata hai
  * 429/401/5xx par doosri key par switch karta hai (user notify)
  * model fallback chain caller (LLMClient) handle karta hai
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

from .base import BaseProvider, ChatResult, ProviderError


class MistralProvider(BaseProvider):
    name = "mistral"
    supports_tools = True
    supports_embeddings = True
    supports_moderation = True
    supports_ocr = True

    def __init__(self, cfg: dict, keyring, notifier=None):
        super().__init__(cfg, keyring, notifier)
        self.base_url = cfg.get("base_url", "https://api.mistral.ai/v1").rstrip("/")
        self.timeout = int(cfg.get("timeout", 180))
        self.max_rotations = int(cfg.get("max_key_rotations_per_call", 6))

    # ------------------------------------------------------------------
    def _request(self, path: str, payload: dict, timeout: Optional[int] = None) -> Dict[str, Any]:
        """POST with automatic key rotation."""
        tried: set = set()
        last_err: Optional[ProviderError] = None
        rotations = max(self.max_rotations, len(self.keyring) or 1)

        for attempt in range(rotations):
            key = self.keyring.acquire(exclude=tried)
            if key is None:
                # every key tried/cooling — wait for the soonest instead of dying
                key = self.keyring.acquire_or_wait(exclude=tried if len(tried) < len(self.keyring) else None)
                if key is None:
                    raise ProviderError(
                        "No API keys available for mistral. Add one with `/keys add <key>` "
                        "or set MISTRAL_API_KEY.", retryable=False)
                tried.discard(key.label)
            tried.add(key.label)

            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}{path}",
                data=body,
                headers={
                    "Authorization": f"Bearer {key.value}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "nexus-agent/1.0",
                },
                method="POST",
            )
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                usage = data.get("usage") or {}
                self.keyring.report_success(key, int(usage.get("total_tokens") or 0))
                data["_key_label"] = key.label
                data["_latency"] = time.time() - t0
                return data
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode("utf-8")[:400]
                except Exception:
                    detail = str(e)
                retry_after = 0.0
                try:
                    retry_after = float(e.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    retry_after = 0.0
                self.keyring.report_failure(key, e.code, detail, retry_after)
                last_err = ProviderError(f"HTTP {e.code}: {detail}", status=e.code,
                                         retryable=e.code in (408, 409, 429) or e.code >= 500)
                if e.code in (400, 404, 422):        # payload/model problem -> key badalne se fayda nahi
                    raise last_err
                if self.keyring.healthy_count > 0:
                    self.notify("warn", f"Switching key after HTTP {e.code} ({key.label})")
                    continue
                time.sleep(min(8, 1.5 ** attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                self.keyring.report_failure(key, None, str(e))
                last_err = ProviderError(f"Network error: {e}", status=None, retryable=True)
                self.notify("warn", f"Network issue on {key.label}: {e}")
                time.sleep(min(8, 1.5 ** attempt))
            except json.JSONDecodeError as e:
                last_err = ProviderError(f"Bad JSON from API: {e}", retryable=True)

        raise last_err or ProviderError("Request failed after all key rotations")

    # ------------------------------------------------------------------
    def chat(self, model: str, messages: List[dict], tools: Optional[List[dict]] = None,
             **params: Any) -> ChatResult:
        payload: Dict[str, Any] = {"model": model, "messages": messages}
        for k in ("temperature", "max_tokens", "top_p", "random_seed", "stop",
                  "presence_penalty", "frequency_penalty", "response_format",
                  "parallel_tool_calls", "prompt_mode"):
            if params.get(k) is not None:
                payload[k] = params[k]
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = params.get("tool_choice", "auto")

        data = self._request("/chat/completions", payload)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):   # multimodal chunk list
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        return ChatResult(
            content=content,
            tool_calls=msg.get("tool_calls") or [],
            model=data.get("model", model),
            provider=self.name,
            key_label=data.get("_key_label", ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason", ""),
            latency=float(data.get("_latency", 0.0)),
            raw=data,
        )

    # ------------------------------------------------------------------
    def stream(self, model: str, messages: List[dict], tools: Optional[List[dict]] = None,
               **params: Any) -> Iterator[str]:
        """SSE streaming with key rotation on connect failure."""
        payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        for k in ("temperature", "max_tokens", "top_p"):
            if params.get(k) is not None:
                payload[k] = params[k]
        if tools:
            payload["tools"] = tools

        tried: set = set()
        for _ in range(max(2, len(self.keyring))):
            key = self.keyring.acquire(exclude=tried)
            if key is None:
                break
            tried.add(key.label)
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {key.value}",
                         "Content-Type": "application/json",
                         "Accept": "text/event-stream"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            self.keyring.report_success(key)
                            return
                        try:
                            j = json.loads(chunk)
                            delta = (j.get("choices") or [{}])[0].get("delta", {})
                            if piece := delta.get("content"):
                                yield piece
                        except json.JSONDecodeError:
                            continue
                self.keyring.report_success(key)
                return
            except urllib.error.HTTPError as e:
                self.keyring.report_failure(key, e.code, str(e))
                self.notify("warn", f"Stream failed on {key.label} (HTTP {e.code}) -> switching key")
            except Exception as e:  # noqa: BLE001
                self.keyring.report_failure(key, None, str(e))
        # last resort: non-streaming
        yield self.chat(model, messages, tools, **params).content

    # ------------------------------------------------------------------
    def embed(self, model: str, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        BATCH = 32
        for i in range(0, len(texts), BATCH):
            batch = [t[:8000] for t in texts[i:i + BATCH]]
            data = self._request("/embeddings", {"model": model, "input": batch})
            rows = sorted(data.get("data", []), key=lambda r: r.get("index", 0))
            out.extend(r["embedding"] for r in rows)
        return out

    def moderate(self, model: str, texts: List[str]) -> List[dict]:
        data = self._request("/moderations", {"model": model, "input": texts})
        return data.get("results", [])

    def ocr(self, model: str, document: dict) -> dict:
        return self._request("/ocr", {"model": model, "document": document},
                             timeout=max(self.timeout, 240))
