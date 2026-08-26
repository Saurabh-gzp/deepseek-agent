"""Web tools: search (DuckDuckGo HTML, no API key) + page fetch to markdown."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from typing import List, Optional

from .base import Risk, ToolRegistry, ToolResult

UA = ("Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36")


def _get(url: str, timeout: int = 25, data: Optional[bytes] = None) -> str:
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def html_to_text(src: str, max_chars: int = 12000) -> str:
    src = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)[^>]*>.*?</\1>", " ", src)
    src = re.sub(r"(?is)<!--.*?-->", " ", src)
    src = re.sub(r"(?i)<br\s*/?>", "\n", src)
    src = re.sub(r"(?i)</(p|div|section|article|li|tr|h[1-6])>", "\n", src)
    src = re.sub(r"(?i)<li[^>]*>", "\n- ", src)
    for i in range(1, 7):
        src = re.sub(rf"(?i)<h{i}[^>]*>", f"\n{'#' * i} ", src)
    src = re.sub(r"(?s)<[^>]+>", " ", src)
    src = html.unescape(src)
    src = re.sub(r"[ \t\xa0]+", " ", src)
    src = re.sub(r"\n\s*\n\s*\n+", "\n\n", src).strip()
    return src[:max_chars]


class WebTools:
    def __init__(self, max_results: int = 6, max_chars: int = 12000):
        self.max_results = max_results
        self.max_chars = max_chars

    # ------------------------------------------------------------------
    # ---- engine fallbacks (a single engine being blocked must not kill research) ----
    def _engine_ddg_html(self, query: str, n: int) -> List[dict]:
        body = urllib.parse.urlencode({"q": query}).encode()
        page = _get("https://html.duckduckgo.com/html/", data=body)
        out, seen = [], set()
        blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'(?:class="result__snippet"[^>]*>(.*?)</a>)?',
            page, re.S)
        for href, title, snip in blocks:
            url = urllib.parse.unquote(href)
            if "uddg=" in url:
                m = re.search(r"uddg=([^&]+)", url)
                if m:
                    url = urllib.parse.unquote(m.group(1))
            t = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            s = html.unescape(re.sub(r"<[^>]+>", "", snip or "")).strip()
            if t and url.startswith("http") and "duckduckgo.com/y.js" not in url \
                    and url not in seen:
                seen.add(url)
                out.append({"title": t, "url": url, "snippet": s[:300]})
            if len(out) >= n:
                break
        return out

    def _engine_ddg_lite(self, query: str, n: int) -> List[dict]:
        body = urllib.parse.urlencode({"q": query}).encode()
        page = _get("https://lite.duckduckgo.com/lite/", data=body)
        out, seen = [], set()
        for href, title, snip in re.findall(
                r'<a[^>]+href="([^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>.*?'
                r'(?:<td[^>]*class="result-snippet"[^>]*>(.*?)</td>)?', page, re.S):
            url = urllib.parse.unquote(href)
            if "uddg=" in url:
                m = re.search(r"uddg=([^&]+)", url)
                if m:
                    url = urllib.parse.unquote(m.group(1))
            t = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            s = html.unescape(re.sub(r"<[^>]+>", "", snip or "")).strip()
            if t and url.startswith("http") and url not in seen:
                seen.add(url)
                out.append({"title": t, "url": url, "snippet": s[:300]})
            if len(out) >= n:
                break
        return out

    def _engine_bing(self, query: str, n: int) -> List[dict]:
        page = _get("https://www.bing.com/search?" + urllib.parse.urlencode({"q": query}))
        out, seen = [], set()
        for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S)[:n * 2]:
            m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            s = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
            if not m:
                continue
            url = html.unescape(m.group(1))
            t = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            sn = html.unescape(re.sub(r"<[^>]+>", "", s.group(1) if s else "")).strip()
            if t and url.startswith("http") and url not in seen:
                seen.add(url)
                out.append({"title": t, "url": url, "snippet": sn[:300]})
            if len(out) >= n:
                break
        return out

    def _engine_mojeek(self, query: str, n: int) -> List[dict]:
        page = _get("https://www.mojeek.com/search?" + urllib.parse.urlencode({"q": query}))
        out, seen = [], set()
        for href, title, snip in re.findall(
                r'<a[^>]+class="ob"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'<p class="s">(.*?)</p>', page, re.S):
            url = html.unescape(href)
            t = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            s = html.unescape(re.sub(r"<[^>]+>", "", snip or "")).strip()
            if t and url.startswith("http") and url not in seen:
                seen.add(url)
                out.append({"title": t, "url": url, "snippet": s[:300]})
            if len(out) >= n:
                break
        return out

    def web_search(self, query: str, max_results: int = 0) -> ToolResult:
        n = max_results or self.max_results
        results: List[dict] = []
        errors: List[str] = []
        # layered fallback so one blocked engine never zeroes the research leg
        for engine in (self._engine_ddg_html, self._engine_ddg_lite,
                       self._engine_bing, self._engine_mojeek):
            try:
                results = engine(query, n)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{engine.__name__}: {type(e).__name__}")
                continue
            if results:
                break

        if not results:
            # last resort: DuckDuckGo instant answer API
            try:
                j = json.loads(_get("https://api.duckduckgo.com/?" + urllib.parse.urlencode(
                    {"q": query, "format": "json", "no_html": 1})))
                if j.get("AbstractText"):
                    results.append({"title": j.get("Heading", query),
                                    "url": j.get("AbstractURL", ""),
                                    "snippet": j["AbstractText"][:400]})
                for topic in (j.get("RelatedTopics") or [])[:n]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append({"title": topic["Text"][:80],
                                        "url": topic.get("FirstURL", ""),
                                        "snippet": topic["Text"][:250]})
            except Exception:
                pass

        if not results:
            hint = "; ".join(errors[-2:]) or "all engines empty"
            return ToolResult(False, error=f"No results for '{query}' ({hint}). "
                                           "Try a simpler 2-3 keyword query and retry.")
        text = "\n\n".join(f"[{i + 1}] {r['title']}\n    {r['url']}\n    {r['snippet']}"
                           for i, r in enumerate(results))
        return ToolResult(True, output=f"Search results for '{query}':\n\n{text}", data=results)

    def web_fetch(self, url: str, max_chars: int = 0) -> ToolResult:
        try:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            raw = _get(url, timeout=30)
            if raw.lstrip().startswith(("{", "[")):
                try:
                    return ToolResult(True, output=json.dumps(json.loads(raw), indent=2)[:self.max_chars])
                except json.JSONDecodeError:
                    pass
            text = html_to_text(raw, max_chars or self.max_chars)
            title = ""
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
            if m:
                title = html.unescape(m.group(1)).strip()
            return ToolResult(True, output=f"# {title}\nSource: {url}\n\n{text}",
                              data={"url": url, "title": title, "chars": len(text)})
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=f"Fetch failed for {url}: {e}")

    def http_request(self, url: str, method: str = "GET", body: str = "",
                     headers_json: str = "") -> ToolResult:
        try:
            hdr = {"User-Agent": UA}
            if headers_json:
                hdr.update(json.loads(headers_json))
            data = body.encode() if body else None
            req = urllib.request.Request(url, data=data, headers=hdr, method=method.upper())
            with urllib.request.urlopen(req, timeout=30) as r:
                out = r.read().decode("utf-8", "ignore")[:8000]
                return ToolResult(True, output=f"HTTP {r.status}\n{out}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=str(e))

    # ------------------------------------------------------------------
    def register(self, reg: ToolRegistry) -> None:
        S = {"type": "string"}
        reg.add("web_search", "Search the web (DuckDuckGo). Use for facts, docs, current info.",
                {"type": "object", "properties": {"query": S, "max_results": {"type": "integer"}},
                 "required": ["query"]},
                self.web_search, Risk.NETWORK)
        reg.add("web_fetch", "Fetch a URL and return readable text/markdown. Use after web_search.",
                {"type": "object", "properties": {"url": S, "max_chars": {"type": "integer"}},
                 "required": ["url"]},
                self.web_fetch, Risk.NETWORK)
        reg.add("http_request", "Make a raw HTTP request (GET/POST) to an API endpoint.",
                {"type": "object", "properties": {
                    "url": S, "method": S, "body": S, "headers_json": S}, "required": ["url"]},
                self.http_request, Risk.NETWORK,
                agents=["supervisor", "coder", "worker", "researcher", "solo"])
