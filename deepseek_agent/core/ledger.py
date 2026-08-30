"""Evidence Ledger — structured record of what the agent actually OBSERVED.

Modern agent-engineering principle (just-in-time context, provenance):
downstream answers cite real observations instead of model world knowledge.

Per turn we keep: goal, intent, tools called (args/result/errors), artifacts,
project scope and server state. Follow-up questions ("isse kya seekha",
"isme kya important hai") resolve against THIS, not against prose memory.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Evidence:
    """One verified observation from a tool call."""
    eid: int
    turn_id: str
    source: str                    # filesystem | shell | web | db | git | server | memory | vision
    operation: str                 # list_dir | read_file | run_shell | ...
    target: str                    # what it was applied to (path / url / command)
    observed: str                  # the factual payload (trimmed)
    ok: bool = True
    verified: bool = True          # deterministic tool success == verified
    artifacts: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"eid": self.eid, "turn": self.turn_id, "source": self.source,
                "operation": self.operation, "target": self.target, "ok": self.ok,
                "verified": self.verified, "observed": self.observed[:6000]}

    def brief(self, limit: int = 700) -> str:
        return (f"[obs#{self.eid}] {self.operation}({self.target}) -> "
                f"{'ok' if self.ok else 'FAILED'}\n{self.observed[:limit]}")


SOURCE_BY_TOOL = {
    "read_file": "filesystem", "list_dir": "filesystem", "find_files": "filesystem",
    "search_files": "filesystem", "write_file": "filesystem", "edit_file": "filesystem",
    "delete_path": "filesystem", "move_path": "filesystem", "see_image": "vision",
    "read_document": "document", "run_shell": "shell", "run_python": "shell",
    "device_info": "device", "system_info": "device", "install_package": "shell",
    "start_server": "server", "stop_server": "server",
    "web_search": "web", "web_fetch": "web", "http_request": "web",
    "browser_navigate": "web", "browser_content": "web", "browser_snapshot": "web",
    "git_status": "git", "git_diff": "git", "git_log": "git", "git_add": "git",
    "git_commit": "git", "sqlite_exec": "db", "sqlite_schema": "db",
    "recall": "memory", "remember": "memory", "search_knowledge": "rag",
}


class EvidenceLedger:
    """Bounded, turn-keyed ledger of observations for the live conversation."""

    MAX_TURNS = 12
    MAX_PER_TURN = 40
    MAX_CHARS = 26000

    def __init__(self) -> None:
        self._turns: List[Dict[str, Any]] = []
        self._seq = 0
        self.current: Dict[str, Any] = self._new_turn("boot", "")

    # ------------------------------------------------------------------
    def _new_turn(self, turn_id: str, goal: str) -> Dict[str, Any]:
        return {"turn_id": turn_id, "goal": goal, "intent": "", "task_id": "",
                "evidence": [], "answer": "", "project": "", "servers": {},
                "ts": time.time()}

    def begin_turn(self, turn_id: str, goal: str, intent: str = "") -> None:
        self.current = self._new_turn(turn_id, goal)
        self.current["intent"] = intent
        self._turns.append(self.current)
        if len(self._turns) > self.MAX_TURNS:
            self._turns.pop(0)

    def set_meta(self, **kw: Any) -> None:
        for k, v in kw.items():
            if v is not None:
                self.current[k] = v

    # ------------------------------------------------------------------
    def record(self, tool: str, args: Dict[str, Any], ok: bool, output: str,
               *, source: str = "", verified: Optional[bool] = None,
               agent: str = "") -> Optional[Evidence]:
        """Store one observation. Returns the Evidence (None if turn is full)."""
        self._seq += 1
        tgt = ""
        for key in ("path", "url", "command", "code", "query", "selector", "sql"):
            if args.get(key):
                tgt = str(args[key])[:160]
                break
        ev = Evidence(
            eid=self._seq, turn_id=self.current["turn_id"],
            source=source or SOURCE_BY_TOOL.get(tool, "tool"),
            operation=tool, target=tgt, observed=(output or "").strip()[:6000],
            ok=ok, verified=(ok if verified is None else verified),
        )
        if len(self.current["evidence"]) < self.MAX_PER_TURN:
            self.current["evidence"].append(ev)
        return ev

    def set_project(self, rel: str) -> None:
        """Which project this turn was about (drives 'usme/isme' resolution)."""
        if rel:
            self.current["project"] = rel

    def current_project(self) -> str:
        for t in reversed(self._turns):
            for key in ("project",):
                v = str(t.get(key) or "")
                if v:
                    return v
        return ""

    def note_artifact(self, path: str) -> None:
        arts = self.current.setdefault("artifacts", [])
        if path and path not in arts and len(arts) < 40:
            arts.append(path)

    def close_turn(self, answer: str = "") -> None:
        self.current["answer"] = (answer or "")[:4000]

    # ------------------------------------------------------------------
    def recent(self, n: int = 3) -> List[Dict[str, Any]]:
        return self._turns[-n:]

    def last_with_evidence(self, back: int = 3) -> Optional[Dict[str, Any]]:
        """Most recent turn that actually observed something (skips chat turns)."""
        for t in reversed(self._turns[-back:] if back > 0 else self._turns):
            if t.get("evidence"):
                return t
        return None

    def all_evidence(self) -> List[Evidence]:
        out: List[Evidence] = []
        for t in self._turns:
            out.extend(t.get("evidence") or [])
        return out

    # ------------------------------------------------------------------
    def context_block(self, max_chars: int = 4200, turns: int = 3) -> str:
        """Prompt-ready block of the last real observations."""
        picks = [t for t in self._turns[-turns:] if t.get("evidence") or t.get("answer")]
        if not picks:
            return ""
        lines = ["## OBSERVED IN THIS CONVERSATION (authoritative evidence)"]
        for t in picks:
            goal = str(t.get("goal") or "")[:90]
            lines.append(f"\n### turn {t.get('turn_id')} — user asked: {goal!r}")
            for ev in (t.get("evidence") or [])[-8:]:
                body = ev.observed.replace("\n", " ⏎ ")[:420]
                lines.append(f"- {ev.operation}({ev.target}) => {body}")
            arts = t.get("artifacts") or []
            if arts:
                lines.append(f"- artifacts: {', '.join(arts[:8])}")
            if t.get("project"):
                lines.append(f"- project scope: {t['project']}")
            if t.get("servers"):
                lines.append(f"- servers: {t['servers']}")
        block = "\n".join(lines)
        return block[:max_chars]

    def fact_digest(self, limit: int = 20) -> List[str]:
        """Plain facts a zero-LLM answer can state safely."""
        facts: List[str] = []
        for t in reversed(self._turns):
            for ev in reversed(t.get("evidence") or []):
                if not ev.ok:
                    continue
                n = ev.observed.count("\n") + 1
                facts.append(f"{ev.operation} on `{ev.target}` -> {n} line(s) observed")
                if len(facts) >= limit:
                    return facts
        return facts

    def stats(self) -> Dict[str, int]:
        return {"turns": len(self._turns),
                "observations": sum(len(t.get("evidence") or []) for t in self._turns),
                "failed": sum(1 for e in self.all_evidence() if not e.ok)}
