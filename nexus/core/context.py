"""AgentContext — shared services container (DI) for all agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..llm.client import LLMClient
from ..memory.store import MemoryStore
from ..rag.engine import RAGEngine
from ..skills.loader import SkillLibrary
from ..tools.base import Risk, ToolRegistry, ToolResult
from ..tools.filesystem import FileSystemTools
from ..tools.shell import ShellTools
from ..tools.web import WebTools
from ..tools.office import OfficeTools
from ..tools.dbms import DbmsTools
from ..tools.gitops import GitTools


class AgentContext:
    """Everything an agent needs: models, tools, skills, memory, RAG, UI, approvals."""

    def __init__(self, config, ui=None, notifier: Optional[Callable[[str, str], None]] = None):
        self.config = config
        self.ui = ui
        self.notify = notifier or (lambda level, msg: None)

        # --- model layer -------------------------------------------------
        self.llm = LLMClient(config, notifier=self.notify)

        # --- knowledge layer ---------------------------------------------
        self.rag = RAGEngine(config, self.llm, self.notify) if config.get("rag.enabled", True) else None
        self.memory = MemoryStore(config.memory_db, self.llm, self.rag) \
            if config.get("memory.enabled", True) else None
        self.skills = SkillLibrary(config.skills_dir, int(config.get("skills.max_active_skills", 3)))

        # --- tools --------------------------------------------------------
        self.tools = ToolRegistry()
        self.fs = FileSystemTools(config.workspace, bool(config.get("safety.sandbox_root_only", True)))
        self.shell = ShellTools(config.workspace,
                                int(config.get("safety.shell.timeout", 120)),
                                config.get("safety.shell.blocked_patterns", []),
                                approval_cb=self._approve_raw)
        self.web = WebTools(int(config.get("tools.web_search.max_results", 6)),
                            int(config.get("tools.web_fetch.max_chars", 12000)))
        self.fs.register(self.tools)
        self.shell.register(self.tools)
        if config.get("tools.web_search.enabled", True):
            self.web.register(self.tools)
        self.office = OfficeTools(config.workspace)
        self.office.register(self.tools)
        self.dbms = DbmsTools(config.workspace)
        self.dbms.register(self.tools)
        self.gitops = GitTools(config.workspace)
        self.gitops.register(self.tools)
        self._register_meta_tools()

        # --- safety --------------------------------------------------------
        from ..safety.guard import SafetyGuard
        self.guard = SafetyGuard(config, self.llm, self.notify)
        if self.ui is not None and hasattr(self.llm, "tick"):
            self.llm.tick = self.ui.tick    # live 'thinking · Xs' indicator

        self.approval_handler: Optional[Callable[[str, dict, str], bool]] = None
        self.state: Dict[str, Any] = {"active_skills": [], "approved_always": set(),
                                      "denied_paths": set()}

    # ------------------------------------------------------------------
    def _register_meta_tools(self) -> None:
        S = {"type": "string"}

        def load_skill(skill_id: str) -> ToolResult:
            body = self.skills.load_body(skill_id)
            if skill_id not in self.state["active_skills"]:
                self.state["active_skills"].append(skill_id)
            if self.ui:
                self.ui.event("skill", f"loaded skill: {skill_id}")
            return ToolResult(True, output=body)

        def list_skills(query: str = "") -> ToolResult:
            if query:
                found = self.skills.search(query, 5)
                if found:
                    return ToolResult(True, output="\n".join(s.summary() for s in found))
            return ToolResult(True, output=self.skills.catalog() or "No skills installed.")

        def search_knowledge(query: str, top_k: int = 5) -> ToolResult:
            if not self.rag:
                return ToolResult(False, error="RAG disabled")
            docs = self.rag.retrieve(query, top_k)
            if not docs:
                return ToolResult(True, output="No relevant knowledge found in the index.")
            return ToolResult(True, output="\n\n".join(
                f"[{d.score}] {d.cite()}\n{d.text[:1200]}" for d in docs))

        def index_knowledge(path: str = "", text: str = "", source: str = "note") -> ToolResult:
            if not self.rag:
                return ToolResult(False, error="RAG disabled")
            if text:
                n = self.rag.index_text(text, f"note://{source}", {"kind": "note"})
                return ToolResult(True, output=f"Indexed note '{source}' ({n} chunks)")
            if path:
                from pathlib import Path
                p = Path(path)
                if not p.is_absolute():
                    p = self.config.workspace / p
                if p.is_dir():
                    st = self.rag.index_directory(p)
                    return ToolResult(True, output=f"Indexed dir: {st}")
                n = self.rag.index_file(p, force=True)
                return ToolResult(True, output=f"Indexed {p.name} ({n} chunks)")
            return ToolResult(False, error="Provide 'path' or 'text'")

        def remember(key: str, value: str, kind: str = "fact", importance: float = 0.6) -> ToolResult:
            if not self.memory:
                return ToolResult(False, error="Memory disabled")
            self.memory.remember(kind, key, value, importance)
            return ToolResult(True, output=f"Remembered [{kind}] {key}")

        def recall(kind: str = "") -> ToolResult:
            if not self.memory:
                return ToolResult(False, error="Memory disabled")
            rows = self.memory.recall(kind or None, 25)
            if not rows:
                return ToolResult(True, output="No memories stored yet.")
            return ToolResult(True, output="\n".join(
                f"[{r['kind']}] {r['key']}: {r['value']}" for r in rows))

        def read_document(path: str) -> ToolResult:
            """OCR / document understanding via provider OCR model."""
            import base64
            from pathlib import Path
            p = Path(path)
            if not p.is_absolute():
                p = self.config.workspace / p
            if not p.exists():
                return ToolResult(False, error=f"Not found: {path}")
            try:
                mime = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
                        "jpeg": "image/jpeg"}.get(p.suffix.lower().lstrip("."), "application/pdf")
                b64 = base64.b64encode(p.read_bytes()).decode()
                key = "document_url" if mime == "application/pdf" else "image_url"
                doc = {"type": key, key: f"data:{mime};base64,{b64}"}
                res = self.llm.ocr(doc)
                pages = res.get("pages", [])
                text = "\n\n".join(pg.get("markdown", "") for pg in pages)
                return ToolResult(True, output=text[:15000] or json.dumps(res)[:4000])
            except Exception as e:  # noqa: BLE001
                return ToolResult(False, error=f"OCR failed: {e}")

        self.tools.add("load_skill",
                       "Load the FULL playbook for a skill id from the catalog before specialised work.",
                       {"type": "object", "properties": {"skill_id": S}, "required": ["skill_id"]},
                       load_skill, Risk.READ_ONLY)
        self.tools.add("list_skills", "List/search available skills.",
                       {"type": "object", "properties": {"query": S}}, list_skills, Risk.READ_ONLY)
        self.tools.add("search_knowledge",
                       "Semantic search over the indexed knowledge base (RAG).",
                       {"type": "object", "properties": {"query": S, "top_k": {"type": "integer"}},
                        "required": ["query"]}, search_knowledge, Risk.READ_ONLY)
        self.tools.add("index_knowledge", "Add a file, folder or note text into the RAG index.",
                       {"type": "object", "properties": {"path": S, "text": S, "source": S}},
                       index_knowledge, Risk.WRITE)
        self.tools.add("remember", "Save a durable fact/preference to long-term memory.",
                       {"type": "object", "properties": {
                           "key": S, "value": S, "kind": S, "importance": {"type": "number"}},
                        "required": ["key", "value"]}, remember, Risk.WRITE)
        self.tools.add("recall", "Retrieve stored facts/preferences from long-term memory.",
                       {"type": "object", "properties": {"kind": S}}, recall, Risk.READ_ONLY)
        self.tools.add("read_document", "Extract text from a PDF/image using OCR.",
                       {"type": "object", "properties": {"path": S}, "required": ["path"]},
                       read_document, Risk.READ_ONLY)

    # ------------------------------------------------------------------
    def _approve_raw(self, action: str, detail: str) -> bool:
        if self.approval_handler:
            return self.approval_handler(action, {"detail": detail}, "system")
        return False

    # ------------------------------------------------------------------
    @staticmethod
    def _action_targets(tool_name: str, args: dict) -> list:
        """Best-effort extraction of the file path(s) a delete/move acts on."""
        import os as _os
        import re as _re
        out: list = []
        if tool_name == "delete_path" and args.get("path"):
            out.append(str(args["path"]))
        elif tool_name == "move_path":
            for k in ("src", "source", "path", "from"):
                if args.get(k):
                    out.append(str(args[k]))
        elif tool_name == "run_shell":
            toks = _re.split(r"\s+", str(args.get("command", "")))
            for i, t in enumerate(toks):
                if t in ("rm", "rmdir", "unlink", "shred", "mv"):
                    for nxt in toks[i + 1:]:
                        if nxt.startswith("-"):
                            continue                    # skip flags
                        out.append(nxt)
                        break
        elif tool_name == "run_python":
            code = str(args.get("code", ""))
            for m in _re.finditer(r"(?:os\.remove|os\.unlink|os\.rmdir|"
                                  r"shutil\.rmtree|\.unlink)\(\s*['\"]([^'\"]+)['\"]", code):
                out.append(m.group(1))
        return [_os.path.normpath(p) for p in out if p and len(p) < 512]

    def _path_frozen(self, tool_name: str, args: dict) -> bool:
        frozen = self.state.get("denied_paths") or set()
        if not frozen:
            return False
        for t in self._action_targets(tool_name, args):
            for f in frozen:
                if t == f or t.endswith("/" + f) or f.endswith("/" + t):
                    return True
        # Hardening: if the user denied tampering with a file, that file
        # name may not appear in shell commands / python code either —
        # otherwise `find -delete`, `python -c os.remove(...)`, `shred` etc.
        # would circumvent the denial (caught in a live test).
        if tool_name in ("run_shell", "run_python", "write_file", "edit_file"):
            blob = str(args)
            for f in frozen:
                base = f.rsplit("/", 1)[-1]
                if (f and f in blob) or (base and len(base) > 3 and base in blob):
                    return True
        return False

    def approve(self, tool_name: str, args: dict, agent: str) -> bool:
        """Permission gate called by every agent before tool execution."""
        mode = self.config.get("safety.approval_mode", "smart")
        if mode == "never":
            return True
        # A path whose deletion/alteration the user already DENIED stays frozen:
        # no renaming, moving or re-deleting around the refusal.
        if self._path_frozen(tool_name, args):
            try:
                self.notify("warn", "blocked: user denied this path earlier "
                            "(rename/move workaround not allowed)")
            except Exception:
                pass
            return False
        tool = self.tools.get(tool_name)
        needs = bool(tool is not None and (tool.approval or tool.risk == Risk.DESTRUCTIVE))
        action = None
        if not needs:
            # policy check: e.g. `run_shell rm -f x` == delete_files
            guard = getattr(self, "guard", None)
            if guard is not None:
                needs, action = guard.needs_approval(tool_name, args)
                if needs:
                    try:
                        self.notify("warn", f"policy: {action} → human approval required")
                    except Exception:
                        pass
        if mode == "always":
            needs = needs or (tool is not None and tool.risk in
                              (Risk.WRITE, Risk.EXECUTE, Risk.DESTRUCTIVE))
        if action is None:
            g2 = getattr(self, "guard", None)
            if g2 is not None:
                try:
                    action = g2.classify_action(tool_name, args)
                except Exception:
                    action = None
        if not needs:
            return True
        _always = self.state.get("approved_always", set())
        if tool_name in _always or (action and f"action:{action}" in _always):
            return True
        if self.approval_handler is None:
            return False
        allowed = self.approval_handler(tool_name, args, agent)
        if allowed == "always":
            # user said 'a' (always): every future call of this tool AND this
            # ACTION is auto-approved — no re-asking (smooth batch deletes).
            self.state.setdefault("approved_always", set()).add(tool_name)
            if action:
                self.state["approved_always"].add(f"action:{action}")
            return True
        if not allowed:
            # Rule: freeze the targets of any action the user denied —
            # so the agent cannot circumvent a denial by renaming/moving those paths.
            for p in self._action_targets(tool_name, args):
                self.state.setdefault("denied_paths", set()).add(p)
        return allowed


import json  # noqa: E402  (used in read_document)
