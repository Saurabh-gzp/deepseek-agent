"""RAG engine: chunking, indexing, retrieval, context packing."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .store import Document, VectorStore

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".deepseek",
             "dist", "build", ".next", ".cache", "target"}


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> List[str]:
    """Structure-aware chunking: markdown headings / code blocks / paragraphs."""
    text = text.replace("\r\n", "\n")
    if len(text) <= size:
        return [text] if text.strip() else []

    # split on headings or blank lines, then pack greedily
    parts = re.split(r"(?m)(?=^#{1,6}\s)|(?<=\n)\n(?=\S)", text)
    chunks: List[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if len(buf) + len(part) <= size:
            buf += part
        else:
            if buf.strip():
                chunks.append(buf.strip())
            if len(part) > size:
                step = size - overlap
                for i in range(0, len(part), step):
                    piece = part[i:i + size]
                    if piece.strip():
                        chunks.append(piece.strip())
                buf = ""
            else:
                buf = (chunks[-1][-overlap:] if chunks and overlap else "") + part
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if c.strip()]


class RAGEngine:
    def __init__(self, config, llm, notifier: Optional[Callable[[str, str], None]] = None):
        self.config = config
        self.llm = llm
        self.notify = notifier or (lambda l, m: None)
        self.store = VectorStore(config.vector_dir / "index.db")
        self.chunk_size = int(config.get("rag.chunk_size", 900))
        self.overlap = int(config.get("rag.chunk_overlap", 150))
        self.top_k = int(config.get("rag.top_k", 6))
        self.min_score = float(config.get("rag.min_score", 0.28))
        self.exts = set(config.get("rag.file_extensions", [".md", ".txt", ".py"]))
        self.max_bytes = int(config.get("rag.max_file_mb", 5)) * 1024 * 1024
        self.enabled = bool(config.get("rag.enabled", True))
        # DeepSeek has no embedding endpoint, so RAG runs keyword-only. Detect
        # this ONCE up front and index silently — not a per-file "Embedding
        # failed" warning for every chunked file.
        self._embeddings_ok = self.llm.supports_embeddings()
        if not self._embeddings_ok:
            self.notify("info", "RAG: keyword-only index (no embedding provider available)")

    # ------------------------------------------------------------------
    def index_text(self, text: str, source: str, meta: Optional[dict] = None,
                   collection: str = "default") -> int:
        if not text.strip():
            return 0
        chunks = chunk_text(text, self.chunk_size, self.overlap)
        if not chunks:
            return 0
        metas = [{**(meta or {}), "chunk_index": i, "total_chunks": len(chunks)}
                 for i in range(len(chunks))]
        if self._embeddings_ok:
            try:
                embs = self.llm.embed(chunks)
            except Exception:  # noqa: BLE001
                # a provider flipped mid-session; degrade this call only
                embs = [[0.0]] * len(chunks)
        else:
            embs = [[0.0]] * len(chunks)          # keyword-only vector
        self.store.delete_source(source)
        return self.store.add(chunks, embs, [source] * len(chunks), metas, collection)

    def index_file(self, path: Path, collection: str = "default", force: bool = False) -> int:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return 0
        if p.suffix.lower() not in self.exts:
            return 0
        try:
            if p.stat().st_size > self.max_bytes:
                return 0
            mtime = p.stat().st_mtime
            if not force and self.store.has_source(str(p), mtime):
                return 0
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return 0
        return self.index_text(text, str(p), {"file": p.name, "ext": p.suffix, "mtime": mtime},
                               collection)

    def index_directory(self, directory: Path, collection: str = "default",
                        force: bool = False, progress: Optional[Callable[[str, int, int], None]] = None) -> Dict:
        directory = Path(directory)
        files: List[Path] = []
        for root, dirs, names in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for n in names:
                fp = Path(root) / n
                if fp.suffix.lower() in self.exts:
                    files.append(fp)
        total_chunks, indexed = 0, 0
        for i, f in enumerate(files):
            c = self.index_file(f, collection, force)
            if c:
                indexed += 1
                total_chunks += c
            if progress:
                progress(str(f.name), i + 1, len(files))
        return {"files_seen": len(files), "files_indexed": indexed, "chunks": total_chunks}

    # ------------------------------------------------------------------
    def retrieve(self, query: str, top_k: int = 0, collection: Optional[str] = None) -> List[Document]:
        if not self.enabled or self.store.count() == 0:
            return []
        k = top_k or self.top_k
        # DeepSeek has no embeddings — keyword search is the normal path,
        # not a per-query "Vector search failed" scare (live: showed on "hy").
        if not self._embeddings_ok:
            return self.store.keyword_search(query, k)
        try:
            qe = self.llm.embed([query])[0]
            docs = self.store.search(qe, k, collection, query_text=query, min_score=self.min_score)
            if docs:
                return docs
        except Exception as e:  # noqa: BLE001
            self.notify("warn", f"Vector search failed: {str(e)[:90]} — keyword fallback")
        return self.store.keyword_search(query, k)

    def context_for(self, query: str, top_k: int = 0, max_chars: int = 6000) -> str:
        docs = self.retrieve(query, top_k)
        if not docs:
            return ""
        out, used = [], 0
        for i, d in enumerate(docs, 1):
            block = f"[KB-{i}] source: {d.cite()} (score {d.score})\n{d.text}\n"
            if used + len(block) > max_chars:
                break
            out.append(block)
            used += len(block)
        return "## Retrieved knowledge\n\n" + "\n".join(out) if out else ""

    def stats(self) -> dict:
        return {"chunks": self.store.count(), "sources": len(self.store.sources()),
                "top_sources": self.store.sources()[:10], "enabled": self.enabled}

    def clear(self) -> int:
        return self.store.clear()
