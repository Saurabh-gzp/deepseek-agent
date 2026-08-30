"""BaseAgent — extended ReAct loop (think -> act -> observe -> reflect).

Har agent:
  * keeps its own role/model chain
  * may use only its allowed tools (least privilege)
  * runs within a step budget and timeout
  * loads skills with progressive disclosure
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..tools.base import ToolRegistry, ToolResult

# §11: does this failed call look like a path/argument problem (vs a real failure)?
_PATHISH = re.compile(
    r"list_dir|read_file|find_files|search_files|not found|No such file|FileNotFound|"
    r"outside workspace|Path outside", re.I)


@dataclass
class AgentStep:
    index: int
    kind: str                 # think | tool | answer | error
    content: str = ""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    duration: float = 0.0


@dataclass
class AgentOutcome:
    agent: str
    ok: bool
    output: str
    steps: List[AgentStep] = field(default_factory=list)
    tokens: int = 0
    model: str = ""
    elapsed: float = 0.0
    error: str = ""

    def step_summary(self) -> str:
        return " → ".join(s.tool or s.kind for s in self.steps[-8:])


class BaseAgent:
    role_key: str = "worker"          # config models.<role_key>
    agent_name: str = "agent"
    allowed_tools: Optional[List[str]] = None   # None = all permitted for this agent
    system_prompt: str = "You are a helpful autonomous agent."
    max_steps: int = 12
    use_skills: bool = True

    def __init__(self, ctx):
        """ctx = AgentContext (llm, tools, skills, rag, memory, config, ui)."""
        self.ctx = ctx
        self.llm = ctx.llm
        self.tools: ToolRegistry = ctx.tools
        self.config = ctx.config
        self.ui = ctx.ui

    # ------------------------------------------------------------------
    def build_system(self, task: str = "", extra: str = "") -> str:
        parts = [self.system_prompt]

        env = [
            f"Workspace directory: {self.config.workspace}",
            f"Today: {time.strftime('%Y-%m-%d %H:%M')}",
            "Platform: Termux/Linux CLI. Prefer POSIX commands, avoid interactive prompts.",
        ]
        # Sutra-style env facts: TELL the model what actually exists so it never
        # fires blind `termux-*/adb/dumpsys` commands (live bug: 17 failed runs).
        try:
            avail = self.ctx.shell.availability()
            if avail:
                env.append("AVAILABLE COMMANDS on this device:\n" + avail)
        except Exception:
            pass
        parts.append("## Environment\n" + "\n".join(f"- {e}" for e in env))

        if self.use_skills and self.ctx.skills:
            cat = self.ctx.skills.catalog(self.agent_name)
            if cat:
                parts.append(
                    "## Available skills (progressive disclosure)\n"
                    "These are expert playbooks written for this system. Call "
                    "`load_skill(skill_id)` to load the full instructions.\n" + cat)
            # Level-1.5: pre-match the most relevant skills for THIS task so the agent
            # doesn't have to guess. Models reliably ignore a passive catalog.
            if task:
                matches = self.ctx.skills.search(task, 2)
                matches = [m for m in matches
                           if "*" in m.agents or self.agent_name in m.agents]
                if matches:
                    ids = ", ".join(f"`{m.id}`" for m in matches)
                    parts.append(
                        f"## ⚑ Required first step\n"
                        f"These skills match this task: {ids}\n"
                        f"Call `load_skill(\"{matches[0].id}\")` as your FIRST tool call and "
                        f"follow its procedure, checklist and anti-patterns. Do not skip this — "
                        f"the playbook contains project standards your output will be judged against.")

        if task and self.ctx.rag and self.config.get("rag.enabled", True):
            kb = self.ctx.rag.context_for(task, max_chars=4000)
            if kb:
                parts.append(kb)

        if extra:
            parts.append(extra)

        parts.append(
            "## Rules\n"
            "- Work autonomously: do not ask the user unless truly blocked.\n"
            "- Verify your work (read files back, run tests) before declaring success.\n"
            "- Use tools instead of guessing file contents or command output.\n"
            "- Be concise in prose; put detail into files you create.\n"
            "- When finished, reply with a final answer WITHOUT tool calls.")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    def tool_specs(self) -> List[dict]:
        return self.tools.specs_for(self.agent_name, self.allowed_tools)

    # v1.10.4 §19/§20: outputs that must never be truncated — the model needs
    # them byte-exact to reason about the task (code it just wrote, etc.)
    FULL_TOOLS = {"read_file", "edit_file", "write_file", "run_python", "git_diff"}
    STALE_CHARS = 700          # older tool results keep only their head

    def _trim_transcript(self, messages: List[dict]) -> int:
        """Rolling truncation: last 3 tool results verbatim, older ones as heads.

        This is the single biggest token saving in the loop — every step re-sends
        the whole history at up to 7,000 chars per result (measured: ~3.3k
        tokens/call, 39.8k tokens for one DESIGN.md).
        """
        saved = 0
        idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        for i in idx[:-3]:
            cur = str(messages[i].get("content") or "")
            if len(cur) <= self.STALE_CHARS:
                continue
            cut = cur[:self.STALE_CHARS] + f"\n…[older result trimmed {len(cur) - self.STALE_CHARS} chars]"
            messages[i]["content"] = cut
            saved += len(cur) - len(cut)
        return saved

    def run(self, task: str, context: str = "", max_steps: int = 0,
            on_step: Optional[Callable[[AgentStep], None]] = None,
            task_id: str = "global", model: Optional[str] = None) -> AgentOutcome:
        t0 = time.time()
        budget = max_steps or min(self.max_steps,
                                  int(self.config.get("autonomy.max_steps_per_agent", 12)))
        timeout = float(self.config.get("autonomy.task_timeout_seconds", 180))
        messages: List[dict] = [
            {"role": "system", "content": self.build_system(task, context)},
            {"role": "user", "content": task},
        ]
        steps: List[AgentStep] = []
        tokens = 0
        model_used = ""
        specs = self.tool_specs()
        consec_fail = 0          # v1.6: brake on repeated tool failures (token fires)
        fail_flagged = False

        for i in range(budget):
            # user pressed Ctrl+C → stop this agent cleanly at the next step
            if self.ctx.state.get("cancelled"):
                return AgentOutcome(self.agent_name, False,
                                    self._partial(steps) or "Stopped by user",
                                    steps, tokens, model_used, time.time() - t0,
                                    error="cancelled by user")
            if time.time() - t0 > timeout:
                return AgentOutcome(self.agent_name, False,
                                    self._partial(steps) or "Timed out",
                                    steps, tokens, model_used, time.time() - t0,
                                    error=f"timeout after {timeout}s")
            self._trim_transcript(messages)      # v1.10.4 token-efficiency
            try:
                res = self.llm.chat(self.role_key, messages, tools=specs or None,
                                    task_id=task_id, model=model)
            except Exception as e:  # noqa: BLE001
                err = str(e)[:300]
                steps.append(AgentStep(i, "error", err, ok=False))
                if on_step:
                    on_step(steps[-1])
                return AgentOutcome(self.agent_name, False, self._partial(steps), steps,
                                    tokens, model_used, time.time() - t0, error=err)

            tokens += res.total_tokens
            model_used = res.model

            if not res.tool_calls:
                answer = (res.content or "").strip()
                steps.append(AgentStep(i, "answer", answer))
                if on_step:
                    on_step(steps[-1])
                return AgentOutcome(self.agent_name, bool(answer), answer or "(empty response)",
                                    steps, tokens, model_used, time.time() - t0)

            # assistant turn with tool calls
            messages.append({"role": "assistant", "content": res.content or "",
                             "tool_calls": res.tool_calls})
            if res.content and res.content.strip():
                steps.append(AgentStep(i, "think", res.content.strip()[:600]))
                if on_step:
                    on_step(steps[-1])

            for call in res.tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                step = AgentStep(i, "tool", tool=name, args=args)

                approved = self.ctx.approve(name, args, self.agent_name)
                if not approved:
                    out = ToolResult(False, error="Action denied by user policy.")
                else:
                    out = self.tools.execute(name, args, self.agent_name)
                step.ok = out.ok
                step.content = out.as_text(1500)
                step.duration = out.duration
                steps.append(step)
                if on_step:
                    on_step(step)

                # v1.10.4 §3/§22: record every observation in the evidence ledger
                # so follow-up turns can cite it instead of guessing.
                led = self.ctx.state.get("ledger")
                if led is not None:
                    try:
                        led.record(name, args, out.ok, out.as_text(3000), agent=self.agent_name)
                        if out.ok and name in ("write_file", "edit_file"):
                            led.note_artifact(str(args.get("path", "")))
                    except Exception:
                        pass

                body = out.as_text(7000)
                # §7: fence anything that came from outside before it re-enters
                # the loop — model-usable text must be marked as untrusted DATA.
                if name in ("web_fetch", "web_search", "http_request", "read_document"):
                    body = self.ctx.guard.wrap_untrusted(body)
                messages.append({"role": "tool", "name": name,
                                 "tool_call_id": call.get("id", f"call_{i}"),
                                 "content": body})
                consec_fail = consec_fail + 1 if not out.ok else 0
                if not out.ok:
                    sig = (name, json.dumps(args, sort_keys=True, default=str)[:400])
                    last = getattr(self, "_last_fail_sig", None)
                    self._last_fail_sig = sig
                    if last == sig:
                        messages.append({"role": "user", "content":
                            "DIAGNOSE: you just repeated the EXACT same failed tool call. "
                            "Do not run it again. Read the ERROR, change the approach "
                            "(different tool/args/path) or FINALIZE honestly."})
                    elif _PATHISH.search(f"{name} {str(args)}"):
                        # §11: a failed READ because of a path argument is a
                        # normalisation problem, not a decision. Diagnose once,
                        # cheaply — do not keep guessing paths.
                        messages.append({"role": "user", "content":
                            f"PATH DIAGNOSIS (read-only failure — no need to fail again): "
                            f"the ACTIVE workspace root is `{self.ctx.config.workspace}`, "
                            f"and every fs tool resolves RELATIVE paths against it. So '.' "
                            f"IS the workspace — `workspace/...` is redundant and wrong here. "
                            f"If you do not know where a file lives, call "
                            f"find_files(pattern='<name>') ONCE and use the path it returns. "
                            f"Do not re-derive the same path a third time."})
                else:
                    self._last_fail_sig = None

            # BRAKE (sutra-style harness rule): 3 consecutive failed tool calls =
            # the agent is guessing commands. Force it onto the safe path NOW.
            if consec_fail >= 3 and not fail_flagged:
                fail_flagged = True
                consec_fail = 0
                messages.append({"role": "user", "content":
                    "HALT: 3+ tool calls failed in a row — you are guessing commands. "
                    "STOP firing new commands. Instead: (1) if the task is a DEVICE "
                    "question, re-run device_info/system_info and report what they "
                    "returned, including explicit 'unavailable' notes — nothing more; "
                    "(2) if you do not know the exact command for something, do ONE "
                    "web_search('how to do X in termux') and follow the first working "
                    "example; (3) otherwise FINALIZE NOW with an honest summary of what "
                    "worked and what is unavailable. No more blind attempts."})

            # wrap-up nudge before the budget ends (never let the loop die silently)
            if i + 1 == budget - 2 and budget > 3:
                messages.append({"role": "user", "content":
                                 "⏰ Tool budget almost over. Finish with what you have "
                                 "and reply with your FINAL answer now (no more tool calls)."})

            # periodic self-reflection nudge
            every = int(self.config.get("autonomy.reflection_every", 4))
            if every and (i + 1) % every == 0 and i + 1 < budget:
                messages.append({"role": "user", "content":
                                 "Checkpoint: briefly state what is done, what remains, and "
                                 "whether your approach is working. Then continue. "
                                 f"You have {budget - i - 1} steps left — if close to done, finish now."})

        # budget exhausted -> ask for a wrap-up answer
        messages.append({"role": "user", "content":
                         "Step budget reached. Give your final answer now with what you achieved, "
                         "what is incomplete, and next steps. No tool calls."})
        try:
            final = self.llm.chat(self.role_key, messages, task_id=task_id, model=model)
            tokens += final.total_tokens
            steps.append(AgentStep(budget, "answer", final.content))
            return AgentOutcome(self.agent_name, False, final.content or "", steps, tokens,
                                final.model, time.time() - t0, error="step budget reached")
        except Exception as e:  # noqa: BLE001
            return AgentOutcome(self.agent_name, False, self._partial(steps), steps, tokens,
                                model_used, time.time() - t0, error=str(e))

    @staticmethod
    def _partial(steps: List[AgentStep]) -> str:
        useful = [s for s in steps if s.kind in ("think", "answer") and s.content]
        return useful[-1].content if useful else ""
