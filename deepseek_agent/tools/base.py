"""Tool framework: schema, risk classes, registry, permission gating.

Design principle (harness engineering):
    "Model proposes — harness executes."
The LLM only returns structured tool calls; validation, permission checks
and the execution harness performs it. This stops prompt-injection from
escalation stops there.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class Risk(str, Enum):
    READ_ONLY = "read_only"     # no side effects
    WRITE = "write"             # workspace ke andar file write
    NETWORK = "network"         # bahar call
    EXECUTE = "execute"         # shell/python run
    DESTRUCTIVE = "destructive" # delete/deploy/irreversible


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    data: Any = None
    duration: float = 0.0
    tool: str = ""

    def as_text(self, limit: int = 6000) -> str:
        body = self.output if self.ok else f"ERROR: {self.error}"
        if len(body) > limit:
            body = body[:limit] + f"\n…[truncated {len(body) - limit} chars]"
        return body or ("(empty output)" if self.ok else "ERROR")


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., ToolResult]
    risk: Risk = Risk.READ_ONLY
    agents: List[str] = field(default_factory=lambda: ["*"])   # who may use it
    approval: bool = False                                     # needs human approval?

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def allowed_for(self, agent: str) -> bool:
        return "*" in self.agents or agent in self.agents


class ToolRegistry:
    def __init__(self, workspace_root=None) -> None:
        self._tools: Dict[str, Tool] = {}
        self.call_log: List[dict] = []
        # v1.10.4 §10: when known, the registry normalises path arguments
        # against the real workspace root before dispatch.
        self.workspace_root = workspace_root

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def add(self, name: str, description: str, parameters: dict, handler,
            risk: Risk = Risk.READ_ONLY, agents: Optional[List[str]] = None,
            approval: bool = False) -> None:
        self.register(Tool(name, description, parameters, handler, risk,
                           agents or ["*"], approval))

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def all(self) -> List[Tool]:
        return list(self._tools.values())

    def specs_for(self, agent: str, only: Optional[List[str]] = None) -> List[dict]:
        out = []
        for t in self._tools.values():
            if only is not None and t.name not in only:
                continue
            if t.allowed_for(agent):
                out.append(t.spec())
        return out

    def execute(self, name: str, args: dict, agent: str = "system") -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(False, error=f"Unknown tool '{name}'. Available: {', '.join(self.names())}",
                              tool=name)
        if not tool.allowed_for(agent):
            return ToolResult(False, error=f"Tool '{name}' not permitted for agent '{agent}'", tool=name)
        # v1.10.4 §10/§11/§21: two argument-intelligence passes BEFORE dispatch.
        #   (a) drop kwargs the handler cannot accept — 'Bad arguments for X' is
        #       a schema mistake, not a task outcome, and it used to cost a whole
        #       agent step + a critic round (live: git_status(cwd='.')).
        #   (b) normalise path arguments against the REAL workspace root, so a
        #       redundant 'workspace/' prefix does not become a failed attempt.
        dropped: List[str] = []
        if self.workspace_root is not None:
            try:
                import inspect as _inspect

                params = _inspect.signature(tool.handler).parameters
                if not any(p.kind is p.VAR_KEYWORD for p in params.values()):
                    allowed = {k for k, p in params.items()
                               if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
                    bad = [k for k in list(args or {}) if k not in allowed]
                    for k in bad:
                        args.pop(k)
                        dropped.append(k)
            except (TypeError, ValueError):
                pass
        norm_notes: List[str] = []
        if self.workspace_root is not None:
            try:
                from .paths import normalize_tool_args
                args, norm_notes = normalize_tool_args(name, args or {}, self.workspace_root)
            except Exception:
                pass
        t0 = time.time()
        try:
            res = tool.handler(**(args or {}))
            if not isinstance(res, ToolResult):
                res = ToolResult(True, output=str(res))
        except TypeError as e:
            res = ToolResult(False, error=f"Bad arguments for {name}: {e}")
        except Exception as e:  # noqa: BLE001
            res = ToolResult(False, error=f"{type(e).__name__}: {e}")
        hints = list(dropped and [f"ignored unsupported arg(s): {', '.join(dropped)}"] or [])
        hints += norm_notes
        if hints:
            head = " │ ".join(hints)
            res.output = f"[args adjusted] {head}\n{res.output}" if res.ok else res.output
            res.error = (f"{res.error}  ({head})" if (not res.ok and hints) else res.error)
        res.duration = time.time() - t0
        res.tool = name
        self.call_log.append({"tool": name, "agent": agent, "ok": res.ok,
                              "args": str(args)[:200], "t": res.duration,
                              "normalised": bool(norm_notes)})
        return res
