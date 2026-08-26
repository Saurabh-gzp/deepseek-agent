"""Terminal UI layer (rich) — mobile/Termux friendly, narrow-width aware.

Design principles used:
  * Always show WHAT the agent is doing (transparency builds trust)
  * Compact glyphs over big boxes (Termux screens are narrow)
  * Colour = meaning, never decoration only
  * Every long operation has live feedback
"""
from __future__ import annotations

import shutil
import threading
import sys
import time
from typing import Any, Dict, List, Optional

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (BarColumn, Progress, SpinnerColumn, TextColumn,
                           TimeElapsedColumn)
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEMES = {
    "cocoa": Theme({   # default — agent activity dim brown, user/result/plan colorful
        "brand": "bold #00e5ff", "accent": "#ff4fd8", "ok": "bold #00ff9c",
        "warn": "bold #ffb300", "err": "bold #ff4b5c", "muted": "#7d7266",
        "agent": "#b08968", "tool": "#a4907c", "think": "italic #8a7460",
        "user": "bold #ffffff", "phase": "bold #c8a887", "key": "#96725b",
    }),
    "cyber": Theme({
        "brand": "bold #00e5ff", "accent": "#ff4fd8", "ok": "bold #00ff9c",
        "warn": "bold #ffb300", "err": "bold #ff4b5c", "muted": "#7a8899",
        "agent": "#8b5cf6", "tool": "#38bdf8", "think": "italic #9aa5b1",
        "user": "bold #ffffff", "phase": "bold #00e5ff",
    }),
    "matrix": Theme({
        "brand": "bold green", "accent": "green", "ok": "bold green",
        "warn": "yellow", "err": "bold red", "muted": "dim green",
        "agent": "green", "tool": "bright_green", "think": "dim green",
        "user": "bold white", "phase": "bold green",
    }),
    "mono": Theme({
        "brand": "bold white", "accent": "white", "ok": "bold white",
        "warn": "bold yellow", "err": "bold red", "muted": "dim white",
        "agent": "white", "tool": "white", "think": "dim white",
        "user": "bold white", "phase": "bold white",
    }),
}

AGENT_ICON = {"supervisor": "🧭", "router": "⚡", "worker": "⚙️", "researcher": "🔍",
              "coder": "💻", "critic": "🔬", "system": "•", "solo": "🤖"}
STATUS_ICON = {"pending": "○", "ready": "◔", "running": "◐", "done": "●",
               "failed": "✕", "skipped": "–", "blocked": "⊘"}

BANNER = r"""
 ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
 ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
 ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
 ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
 ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
 ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
"""
BANNER_SMALL = "◤ N E X U S ◢"


class Tick:
    """Live processing indicator + elapsed timer.

    While an LLM call runs, a '⣾ thinking · worker · 14s' status line
    keeps going (background thread updates every 0.5s) — the TUI never
    looks frozen (user feedback #3)."""

    def __init__(self, console: Console, label: str):
        self.console = console
        self.label = label
        self._sp = None
        self._thread = None
        self._stop = False
        self._t0 = 0.0

    def _render(self) -> None:
        el = time.time() - self._t0
        m, s = int(el) // 60, int(el) % 60
        txt = f"[muted]{self.label} · {m}:{s:02d}[/]" if m else f"[muted]{self.label} · {s}s[/]"
        try:
            self._sp.update(txt)
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop:
            self._render()
            time.sleep(0.5)

    def __enter__(self):
        if self.console.is_terminal:
            self._t0 = time.time()
            self._sp = self.console.status(f"[muted]{self.label} · 0s[/]", spinner="dots")
            self._sp.start()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        if self._sp is not None:
            try:
                self._sp.stop()
            except Exception:
                pass
        return False


class UI:
    def __init__(self, theme: str = "cocoa", verbose: bool = True):
        self.console = Console(theme=THEMES.get(theme, THEMES["cocoa"]), soft_wrap=False)
        self.verbose = verbose
        self.width = shutil.get_terminal_size((80, 24)).columns
        self.narrow = self.width < 70
        self._live: Optional[Live] = None
        self._log: List[str] = []
        # autocomplete (prompt_toolkit optional)
        self.completer = None
        self.history_path = None
        self._pt_session = None

    def _pt(self):
        """prompt_toolkit session — '/ ' pe command menu, arrow-key history."""
        if self._pt_session is None:
            from .completer import make_prompt_session
            self._pt_session = make_prompt_session(self.completer, self.history_path)
        return self._pt_session

    def tick(self, label: str) -> Tick:
        """Context manager: live spinner + timer. `with ui.tick("supervisor planning"):`"""
        return Tick(self.console, label)

    # ---------------- primitives ----------------
    def print(self, *a, **kw) -> None:
        self.console.print(*a, **kw)

    def rule(self, text: str = "") -> None:
        self.console.print(Rule(Text(text, style="muted"), style="muted"))

    def banner(self, version: str, provider: str, keys: int, model: str, workspace: str) -> None:
        c = self.console
        if self.narrow:
            c.print(Align.center(Text(BANNER_SMALL, style="brand")))
        else:
            c.print(Text(BANNER, style="brand"))
        info = Table.grid(padding=(0, 2))
        info.add_column(style="muted", justify="right")
        info.add_column(style="accent")
        info.add_row("version", version)
        info.add_row("provider", f"{provider} · {keys} key(s)")
        info.add_row("brain", model)
        info.add_row("workspace", workspace)
        c.print(Panel(info, title="[brand]Autonomous Agent[/]",
                      subtitle="[muted]/help for commands[/]", border_style="brand", expand=False))

    # ---------------- events ----------------
    def event(self, kind: str, msg: str) -> None:
        icons = {"info": "ℹ", "warn": "⚠", "error": "✕", "ok": "✓", "skill": "📘",
                 "retry": "↻", "key": "🔑", "tool": "→"}
        styles = {"info": "muted", "warn": "warn", "error": "err", "ok": "ok",
                  "skill": "agent", "retry": "muted", "key": "key", "tool": "tool"}
        self.console.print(f"  [{styles.get(kind, 'muted')}]{icons.get(kind, '·')} {msg}[/]")
        self._log.append(f"{kind}: {msg}")

    def notify(self, level: str, msg: str) -> None:
        """Bound to provider/LLM notifier (key switching etc.)."""
        if level == "warn" and ("key" in msg.lower() or "switch" in msg.lower()):
            self.event("key", msg)
        elif level in ("warn", "error"):
            self.event(level, msg)
        elif self.verbose:
            self.event("info", msg)

    def phase(self, name: str, detail: str = "", quiet: bool = False) -> None:
        if quiet and not self.verbose:
            return
        bar = "─" * max(2, min(24, self.width - len(name) - len(detail) - 14))
        self.console.print(f"\n[phase]▸ {name}[/] [muted]{bar} {detail}[/]")

    def user_echo(self, text: str) -> None:
        self.console.print(f"[user]❯[/] {text}")

    # ---------------- planning / tasks ----------------
    def route_info(self, d: Dict[str, Any]) -> None:
        if not self.verbose:
            return
        self.console.print(
            f"  [muted]intent={d.get('intent')} · complexity={d.get('complexity')} · "
            f"orchestrate={d.get('needs_orchestration')} · {str(d.get('reason', ''))[:60]}[/]")

    def show_plan(self, plan: Dict[str, Any], dag) -> None:
        t = Table(box=None, padding=(0, 1), show_header=True, header_style="muted")
        t.add_column("#", style="muted", width=3)
        t.add_column("task", style="white", overflow="fold")
        t.add_column("agent", style="agent", width=11)
        t.add_column("deps", style="muted", width=8)
        for i, task in enumerate(dag.order(), 1):
            t.add_row(str(i), task.title[:46],
                      f"{AGENT_ICON.get(task.agent, '•')} {task.agent}",
                      ",".join(task.depends_on) or "—")
        body = Group(
            Text(plan.get("strategy", "")[:300], style="muted"),
            Text(""),
            t,
        )
        self.console.print(Panel(body, title=f"[brand]PLAN[/] [muted]{len(dag)} tasks[/]",
                                 border_style="accent", expand=not self.narrow))

    def task_start(self, task) -> None:
        icon = AGENT_ICON.get(task.agent, "•")
        self.console.print(f"\n[agent]{icon} {task.agent}[/] [muted]›[/] [white]{task.title}[/] "
                           f"[muted]({task.id})[/]")

    def task_step(self, task, step) -> None:
        if not self.verbose:
            return
        if step.kind == "think":
            txt = step.content.strip().replace("\n", " ")[:110]
            if txt:
                self.console.print(f"    [think]… {txt}[/]")
        elif step.kind == "tool":
            mark = "[ok]✓[/]" if step.ok else "[err]✕[/]"
            arg = ""
            for k in ("path", "command", "query", "url", "skill_id", "src", "code"):
                if k in step.args:
                    arg = str(step.args[k])[:52]
                    break
            self.console.print(f"    {mark} [tool]{step.tool}[/] [muted]{arg}[/] "
                               f"[muted]{step.duration:.1f}s[/]")
            if not step.ok and step.content:
                err_line = next((l for l in str(step.content).splitlines()
                                 if l.strip()), "")[:90]
                self.console.print(f"      [err]↳ {err_line}[/]")
        elif step.kind == "error":
            self.console.print(f"    [err]✕ {step.content[:110]}[/]")

    def verdict(self, task, v: Dict[str, Any]) -> None:
        col = {"pass": "ok", "partial": "warn", "fail": "err"}.get(v.get("verdict"), "muted")
        issues = "; ".join(v.get("issues", []))[:90]
        self.console.print(f"    [🔬] [{col}]{v.get('verdict', '?')}[/] "
                           f"[muted]score {v.get('score', 0)}[/]" +
                           (f" [muted]· {issues}[/]" if issues else ""))

    def task_end(self, task) -> None:
        icon = STATUS_ICON.get(task.status.value, "•")
        col = {"done": "ok", "failed": "err", "blocked": "err", "skipped": "muted"}.get(
            task.status.value, "muted")
        self.console.print(f"  [{col}]{icon} {task.id} {task.status.value}[/] "
                           f"[muted]{task.elapsed:.1f}s · {task.tokens} tok"
                           + (f" · {task.error[:50]}" if task.error else "") + "[/]")

    # ---------------- output ----------------
    def answer(self, text: str, title: str = "RESULT") -> None:
        try:
            body: Any = Markdown(text) if any(m in text for m in ("#", "**", "```", "- ")) else Text(text)
        except Exception:
            body = Text(text)
        self.console.print(Panel(body, title=f"[brand]{title}[/]", border_style="brand",
                                 padding=(1, 2) if not self.narrow else (0, 1)))

    def stats_line(self, report) -> None:
        self.console.print(
            f"[muted]⏱ {report.elapsed:.1f}s · {len(report.tasks)} tasks · "
            f"{report.tokens} tokens · replans {report.replans} · "
            f"{'verified' if report.verified else 'unverified'}[/]")

    def table(self, title: str, columns: List[str], rows: List[List[str]],
              styles: Optional[List[str]] = None) -> None:
        t = Table(title=f"[brand]{title}[/]", box=None, header_style="muted", padding=(0, 1))
        for i, c in enumerate(columns):
            t.add_column(c, style=(styles[i] if styles and i < len(styles) else "white"),
                         overflow="fold")
        for r in rows:
            t.add_row(*[str(x) for x in r])
        self.console.print(t)

    def code(self, text: str, lang: str = "python") -> None:
        self.console.print(Syntax(text, lang, theme="monokai", line_numbers=False,
                                  word_wrap=True))

    def error(self, msg: str) -> None:
        self.console.print(Panel(Text(msg, style="err"), border_style="err", title="[err]ERROR[/]"))

    # ---------------- interaction ----------------
    def ask(self, prompt: str = "", default: str = "", secret: bool = False) -> str:
        """REPL input — prompt_toolkit (autocomplete+history) ya rich fallback."""
        import re as _re
        nl = "\n" * (len(prompt) - len(prompt.lstrip("\n")))
        plain = _re.sub(r"\[/?[a-z_ #0-9;]*\]", "", prompt).strip()
        pt = self._pt() if (self.completer and not secret) else None
        if nl and pt is not None:
            self.console.print()
        if pt is not None:
            try:
                from prompt_toolkit.formatted_text import HTML as PTHTML
                text = pt.prompt(PTHTML(
                    f"<ansicyan><b>{plain or '❯'}</b></ansicyan> "),
                    is_password=secret)
                return text if (text or not default) else default
            except Exception:
                pass                                        # fallback niche
        if default:
            return Prompt.ask(f"[user]{plain or '❯'}[/]", default=default,
                              console=self.console, password=secret)
        return Prompt.ask(f"[user]{plain or '❯'}[/]", console=self.console,
                          password=secret)

    def confirm(self, question: str, default: bool = False) -> bool:
        return Confirm.ask(f"[warn]{question}[/]", default=default, console=self.console)

    def approval(self, tool: str, args: dict, agent: str) -> str:
        """Returns 'yes' | 'no' | 'always'."""
        detail = "\n".join(f"  {k}: {str(v)[:200]}" for k, v in (args or {}).items())
        self.console.print(Panel(
            Text(f"agent : {agent}\ntool  : {tool}\n{detail}", style="warn"),
            title="[warn]⚠ APPROVAL REQUIRED[/]", border_style="warn"))
        ans = Prompt.ask("[warn]Allow?[/]", choices=["y", "n", "a"], default="n",
                         console=self.console)
        return {"y": "yes", "n": "no", "a": "always"}[ans]

    # ---------------- progress ----------------
    def spinner(self, text: str):
        return self.console.status(f"[accent]{text}[/]", spinner="dots")

    def progress(self) -> Progress:
        return Progress(SpinnerColumn(style="accent"),
                        TextColumn("[muted]{task.description}[/]"),
                        BarColumn(bar_width=20 if self.narrow else 30),
                        TextColumn("[muted]{task.completed}/{task.total}[/]"),
                        TimeElapsedColumn(), console=self.console, transient=True)
