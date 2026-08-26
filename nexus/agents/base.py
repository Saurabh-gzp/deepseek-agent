"""BaseAgent — extended ReAct loop (think -> act -> observe -> reflect).

Har agent:
  * keeps its own role/model chain
  * may use only its allowed tools (least privilege)
  * runs within a step budget and timeout
  * loads skills with progressive disclosure
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..tools.base import ToolRegistry, ToolResult


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

    def run(self, task: str, context: str = "", max_steps: int = 0,
            on_step: Optional[Callable[[AgentStep], None]] = None,
            task_id: str = "global") -> AgentOutcome:
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

        for i in range(budget):
            if time.time() - t0 > timeout:
                return AgentOutcome(self.agent_name, False,
                                    self._partial(steps) or "Timed out",
                                    steps, tokens, model_used, time.time() - t0,
                                    error=f"timeout after {timeout}s")
            try:
                res = self.llm.chat(self.role_key, messages, tools=specs or None, task_id=task_id)
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
                messages.append({"role": "tool", "name": name,
                                 "tool_call_id": call.get("id", f"call_{i}"),
                                 "content": out.as_text(7000)})

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
            final = self.llm.chat(self.role_key, messages, task_id=task_id)
            tokens += final.total_tokens
            steps.append(AgentStep(budget, "answer", final.content))
            return AgentOutcome(self.agent_name, True, final.content, steps, tokens,
                                final.model, time.time() - t0, error="step budget reached")
        except Exception as e:  # noqa: BLE001
            return AgentOutcome(self.agent_name, False, self._partial(steps), steps, tokens,
                                model_used, time.time() - t0, error=str(e))

    @staticmethod
    def _partial(steps: List[AgentStep]) -> str:
        useful = [s for s in steps if s.kind in ("think", "answer") and s.content]
        return useful[-1].content if useful else ""
