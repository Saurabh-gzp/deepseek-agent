"""Nexus CLI — interactive REPL + one-shot mode."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import get_config
from ..core.context import AgentContext
from ..orchestrator.engine import Orchestrator
from .ui import UI

HELP = """
[brand]COMMANDS[/]
  [accent]/help[/]                 show this help
  [accent]/status[/]               provider keys, models, usage stats
  [accent]/key[/]                  🔑 key manager menu (add/delete/test)
  [accent]/keys[/]                 API key health (add: /keys add <key>)
  [accent]/skills [q][/]           list or search skills
  [accent]/skill <id>[/]           show a skill's full playbook
  [accent]/rag[/]                  knowledge base stats
  [accent]/index <path>[/]         index a file/folder into RAG
  [accent]/forget-index[/]         clear the RAG index
  [accent]/memory[/]               memory stats + stored facts
  [accent]/remember k=v[/]         save a preference
  [accent]/sessions[/]             list past sessions
  [accent]/resume <n|id>[/]        resume a session (number or id; bare /resume = latest)
  [accent]/tools[/]                list available tools
  [accent]/projects[/]             list project folders in workspace
  [accent]/plan <goal>[/]          plan only, do not execute
  [accent]/auto <goal>[/]          force full autonomous orchestration
  [accent]/agent <name> <task>[/]  run one specific agent directly
  [accent]/cd <path>[/]            change workspace
  [accent]/mode <smart|always|never>[/]  approval mode
  [accent]/verbose[/]              toggle step-by-step output
  [accent]/clear[/]                clear screen
  [accent]/exit[/]                 quit

[muted]Anything else = a goal for the autonomous agent.[/]
"""


class NexusApp:
    def __init__(self, config_path: Optional[str] = None, theme: Optional[str] = None,
                 verbose: bool = True, approval: Optional[str] = None,
                 workspace: Optional[str] = None):
        self.config = get_config(config_path)
        if workspace:
            self.config.set("app.workspace", workspace)
        if approval:
            self.config.set("safety.approval_mode", approval)
        self.ui = UI(theme or self.config.get("app.theme", "cyber"), verbose)
        self.ctx = AgentContext(self.config, self.ui, self.ui.notify)
        self.ctx.approval_handler = self._approval
        self.orchestrator = Orchestrator(self.ctx)
        self.running = True

    # ------------------------------------------------------------------
    def _approval(self, tool: str, args: dict, agent: str):
        """y=True / n=False / a='always' (tool + action dono always-mark)."""
        ans = self.ui.approval(tool, args, agent)
        if ans == "always":
            self.ctx.state.setdefault("approved_always", set()).add(tool)
            try:
                act = self.ctx.guard.classify_action(tool, args)
                if act:
                    self.ctx.state["approved_always"].add(f"action:{act}")
            except Exception:
                pass
            return True
        return ans == "yes"

    # ------------------------------------------------------------------
    def _wire_completer(self) -> None:
        """Slash-autocomplete: / shows all commands, /skill completes ids, /agent completes names."""
        try:
            from .completer import NexusCompleter, _HAS_PT
            if not _HAS_PT:
                return
            hints = {
                "/skill": sorted(self.ctx.skills.skills.keys()),
                "/skills": [""],
                "/agent": ["researcher", "worker", "coder", "critic", "supervisor"],
                "/mode": ["smart", "always", "never"],
                "/cd": ["../", "./workspace"],
            }
            self.ui.completer = NexusCompleter(hints)
            self.ui.history_path = self.config.data_dir / "history"
        except Exception:
            pass

    def maybe_first_run_setup(self) -> None:
        """First run with no keys → mini setup wizard.

        The user picks a provider, pastes a key, and the key is verified LIVE
        (invalid keys are never saved). Multiple keys can be added.
        """
        import sys as _sys
        from ..core.keymanager import KeyManager
        marker = self.config.data_dir / "setup_done"
        try:
            if marker.exists():
                return
            if not _sys.stdin.isatty():               # non-interactive/one-shot
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("skipped-non-tty")
                return
            reg = self.ctx.llm.registry
            if reg.total_keys() > 0:
                return
        except Exception:
            return
        self.ui.rule("SETUP — chalo shuru karein 🚀")
        self.ui.print("  [muted]No API keys found. The agent needs at least "
                      "1 key to work.[/]")
        km = KeyManager(self.config)
        provs = [p for p, c in (self.config.get("providers", {}) or {}).items()
                 if p != "default" and isinstance(c, dict) and c.get("enabled")]
        self.ui.print("  Select a provider:")
        for i, p in enumerate(provs, 1):
            self.ui.print(f"   [accent]{i}[/] {p}"
                          + ("  [muted](recommended — free: console.mistral.ai)[/]"
                             if p == "mistral" else ""))
        self.ui.print("   [muted]0 skip (you can add one later with /key)[/]")
        try:
            sel = self.ui.ask("provider").strip()
        except (KeyboardInterrupt, EOFError):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("skipped")
            return
        if not sel.isdigit() or not (1 <= int(sel) <= len(provs)):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("skipped")
            self.ui.print("  [muted]alright — add a key anytime with /key[/]")
            return
        prov = provs[int(sel) - 1]
        added = 0
        while True:
            try:
                newk = self.ui.ask(f"Paste your {prov} API key", secret=True).strip()
            except (KeyboardInterrupt, EOFError):
                break
            if len(newk) < 16:
                self.ui.event("error", "key looks too short — try again")
            else:
                dup = set(km.load(prov)) | {k.value for k in
                                            getattr(reg.keyrings.get(prov), "keys", [])}
                if newk in dup:
                    self.ui.event("warn", "this key is already added")
                else:
                    with self.ui.spinner(f"verifying against {prov} API…"):
                        ok, msg = self._test_key(prov, newk)
                    if ok:
                        km.add(prov, newk)
                        reg.ensure_provider(prov, [newk])   # ring + provider live
                        added += 1
                        self.ui.event("ok", f"key VALID ✓ ({msg}) — saved keys/{prov}.json")
                    else:
                        self.ui.event("error", f"key INVALID ({msg}) — not saved")
            try:
                if not self.ui.confirm("add another key?", added < 1):
                    break
            except (KeyboardInterrupt, EOFError):
                break
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("done")
        if added:
            self.ui.event("ok", f"setup complete — {added} key(s) ready. Starting the agent…")
            self.ui.print("")

    def start(self) -> None:
        self._wire_completer()
        try:
            self.ui.config_opt_fancy = bool(self.config.get("ui.fancy_input", False))
        except Exception:
            pass
        reg = self.ctx.llm.registry
        self.ui.banner(
            self.config.get("app.version", "1.0.0"),
            reg.default_name or "none",
            reg.total_keys(),
            self.config.model_for("supervisor"),
            str(self.config.workspace),
        )
        n_sk = len(self.ctx.skills.skills)
        rag_n = self.ctx.rag.store.count() if self.ctx.rag else 0
        self.ui.print(f"[muted]  {n_sk} skills · {len(self.ctx.tools.names())} tools · "
                      f"{rag_n} KB chunks · approval={self.config.get('safety.approval_mode')}[/]\n")
        self.ui.print("[muted]  Ctrl+C stops a running task — then type a correction; "
                      "your conversation context is always kept.[/]\n")
        if self.ctx.memory:
            self.ctx.memory.start_session()
        if self.config.get("rag.auto_index_workspace", True) and self.ctx.rag:
            self._auto_index()

    def _auto_index(self) -> None:
        try:
            ws = self.config.workspace
            files = [p for p in ws.rglob("*") if p.is_file()]
            if not files or len(files) > 800:
                return
            with self.ui.spinner("indexing workspace for RAG…"):
                st = self.ctx.rag.index_directory(ws)
            if st["files_indexed"]:
                self.ui.event("ok", f"indexed {st['files_indexed']} file(s), {st['chunks']} chunks")
        except Exception as e:  # noqa: BLE001
            self.ui.event("warn", f"auto-index skipped: {str(e)[:70]}")

    # ------------------------------------------------------------------
    def _install_ctrl_c(self) -> None:
        """Ctrl+C during a run: 1st = graceful stop (finish-safe), 2nd = force.
        Idle at the prompt: Ctrl+C still exits the REPL (standard behaviour)."""
        import signal

        def handler(signum, frame):  # noqa: ARG001
            if getattr(self, "_running", False):
                self.orchestrator.cancel()
                # NOTE: never use rich/console here — a live spinner is active and
                # console.print from a signal handler crashes the process.
                try:
                    os.write(1, b"\n  \xe2\x8f\xb9 stopping... (Ctrl+C again = force)\n")
                except Exception:
                    pass
            else:
                raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGINT, handler)
        except (ValueError, OSError):
            pass

    def repl(self) -> None:
        self.maybe_first_run_setup()
        self.start()
        self._install_ctrl_c()
        while self.running:
            try:
                # NOTE: no leading \n here — blank-enter and terminal-resize
                # repaints must not stack empty lines above the prompt.
                line = self.ui.ask("[user]nexus ❯[/]").strip()
            except (KeyboardInterrupt, EOFError):
                self.ui.print("\n[muted]bye 👋[/]")
                break
            if not line:
                continue
            try:
                self._running = True
                self.dispatch(line)
            except KeyboardInterrupt:
                self.orchestrator.cancel()
                self.ui.print("\n[warn]⏹ stopped by user — type a message to redirect "
                              "or a new task (context is kept)[/]")
            except Exception as e:  # noqa: BLE001
                self.ui.error(f"{type(e).__name__}: {e}")
                if os.getenv("NEXUS_DEBUG"):
                    import traceback
                    self.ui.print(traceback.format_exc())
            finally:
                self._running = False
                try:
                    self.ctx.state.pop("cancelled", None)
                    self.orchestrator.cancelled = False
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def dispatch(self, line: str) -> None:
        if line.startswith("/"):
            parts = line[1:].split(" ", 1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            fn = getattr(self, f"cmd_{cmd.replace('-', '_')}", None)
            if fn:
                fn(arg)
            else:
                self.ui.event("warn", f"unknown command /{cmd} — try /help")
            return
        self.run_goal(line)

    # ---------------- goal execution ----------------
    def run_goal(self, goal: str, force: bool = False) -> None:
        report = self.orchestrator.handle(goal, force_orchestration=force)
        self.ui.print()
        self.ui.answer(report.final or "(no output)")
        self.ui.stats_line(report)

    # ---------------- commands ----------------
    def cmd_help(self, _: str = "") -> None:
        self.ui.print(HELP)

    def cmd_exit(self, _: str = "") -> None:
        self.running = False
        self.ui.print("[muted]bye 👋[/]")

    cmd_quit = cmd_exit
    cmd_q = cmd_exit

    def cmd_clear(self, _: str = "") -> None:
        self.ui.console.clear()

    def cmd_verbose(self, _: str = "") -> None:
        self.ui.verbose = not self.ui.verbose
        self.ui.event("ok", f"verbose = {self.ui.verbose}")

    def cmd_status(self, _: str = "") -> None:
        s = self.ctx.llm.stats.snapshot()
        rows = [["calls", s["calls"]], ["tokens", s["total_tokens"]],
                ["errors", s["errors"]], ["model fallbacks", s["fallbacks"]],
                ["avg latency", f"{s['avg_latency']}s"]]
        self.ui.table("USAGE", ["metric", "value"], rows, ["muted", "accent"])
        if s["by_model"]:
            self.ui.table("BY MODEL", ["model", "calls"],
                          [[k, v] for k, v in s["by_model"].items()], ["white", "accent"])
        self.cmd_keys("")

    def cmd_key(self, _: str = "") -> None:
        """🔑 Interactive key manager — providers → keys → add/delete/test."""
        from ..core.keymanager import KeyManager, mask as mask_key, unified_keys
        km = KeyManager(self.config)
        while True:                                   # ---- level 1: providers
            provs = list(self.ctx.llm.registry.keyrings.keys()) or \
                [p for p, c in (self.config.get("providers", {}) or {}).items()
                 if p != "default" and isinstance(c, dict) and c.get("enabled")]
            self.ui.rule("🔑 KEY MANAGER")
            if not provs:
                self.ui.event("warn", "no providers enabled (config/config.yaml)")
                return
            rows = [[str(i + 1), p,
                     str(len(unified_keys(km.load(p),
                                          self.ctx.llm.registry.keyrings.get(p))))]
                    for i, p in enumerate(provs)]
            self.ui.table("PROVIDERS", ["#", "provider", "keys"], rows,
                          ["muted", "accent", "white"])
            self.ui.print("[muted]  0. exit[/]")
            try:
                sel = self.ui.ask("select provider").strip()
            except (KeyboardInterrupt, EOFError):
                return
            if sel in ("0", "exit", "q", ""):
                return
            if not sel.isdigit() or not (1 <= int(sel) <= len(provs)):
                self.ui.event("warn", "invalid choice")
                continue
            prov = provs[int(sel) - 1]
            while True:                               # ---- level 2: keys
                ring = self.ctx.llm.registry.keyrings.get(prov)
                ukeys = unified_keys(km.load(prov), ring)
                self.ui.rule(f"🔑 {prov.upper()} KEYS")
                if not ukeys:
                    self.ui.print("  [muted](no keys yet — press 'a' to add one)[/]")
                stats = {}
                if prov in self.ctx.llm.key_status():
                    stats = {k["masked"]: k for k in self.ctx.llm.key_status()[prov]}
                rows = []
                for u in ukeys:
                    st = stats.get(u["masked"], {})
                    state = st.get("state", "saved")
                    rows.append([str(u["n"]), u["masked"], u["src"],
                                 state + (f" ({st['cooldown_left']}s)" if st.get("cooldown_left") else ""),
                                 str(st.get("success", "–")), str(st.get("failures", "–")),
                                 str(st.get("tokens", "–"))])
                if rows:
                    self.ui.table("KEYS", ["#", "key", "source", "state", "ok", "fail", "tokens"],
                                  rows, ["muted", "accent", "muted", "white", "ok", "err", "muted"])
                self.ui.print(
                    "\n  [accent]a[/] add key   [accent]d N[/] delete #N   "
                    "[accent]t[/] test (menu)   [accent]b[/] back   [accent]0[/] exit")
                try:
                    act = self.ui.ask("action").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    return
                if act in ("0", "exit", "q"):
                    return
                if act in ("b", "back"):
                    break
                if act == "a":
                    self._key_add(km, prov, ring)
                elif act.startswith("d") and act[1:].strip().isdigit():
                    n = int(act[1:].strip())
                    u = next((x for x in ukeys if x["n"] == n), None)
                    if not u:
                        self.ui.event("warn", "invalid key number")
                        continue
                    if u["src"] != "keys/":
                        self.ui.event("warn", f"{u['masked']} came from .env — "
                                      "delete it from the .env file")
                        continue
                    if not self.ui.confirm(f"delete key {u['masked']} ?", False):
                        continue
                    km.remove_value(prov, u["value"])
                    if ring:
                        ring.remove_key(u["value"])
                    self.ui.event("ok", f"deleted {u['masked']} (keys/{prov}.json)")
                elif act == "t" or act.startswith("t"):
                    self._key_test_menu(prov, ukeys)
                elif act:
                    self.ui.event("warn", "a | d N | t | b | 0 samajh aata hai")

    # ------------------------------------------------------------------
    def _key_add(self, km, prov: str, ring) -> None:
        """Add + DUPLICATE check + LIVE VERIFY — invalid keys are never saved."""
        try:
            newk = self.ui.ask("paste API key", secret=True).strip()
        except (KeyboardInterrupt, EOFError):
            return
        if len(newk) < 16:
            self.ui.event("error", "key looks too short — check it")
            return
        # duplicate check: across both the keys/ files and the ring (.env keys)
        existing = set(km.load(prov)) | {k.value for k in getattr(ring, "keys", [])}
        if newk in existing:
            self.ui.event("warn", "this key is ALREADY added (in keys/ or .env) — not a duplicate")
            return
        with self.ui.spinner(f"verifying key against {prov} API…"):
            ok, msg = self._test_key(prov, newk)
        if not ok:
            self.ui.event("error", f"key INVALID — not saved · {msg}")
            retry = self.ui.confirm("try again?", True)
            if retry:
                self._key_add(km, prov, ring)
            return
        km.add(prov, newk)
        self.ctx.llm.registry.ensure_provider(prov, [newk])   # ring + provider live
        self.ui.event("ok", f"key VALID ✓ ({msg}) — saved → keys/{prov}.json")

    def _key_test_menu(self, prov: str, ukeys: list) -> None:
        """t — first ask which key; 'all' at the top."""
        if not ukeys:
            self.ui.event("warn", "no keys to test")
            return
        self.ui.print("\n  [accent]all[/] — test every key")
        for u in ukeys:
            self.ui.print(f"  [accent]{u['n']}[/] {u['masked']} [muted]({u['src']})[/]")
        self.ui.print("  [muted]b[/] back")
        try:
            sel = self.ui.ask("which key to test?").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return
        if sel in ("b", "", "0"):
            return
        if sel == "all":
            rows = []
            with self.ui.spinner(f"testing {len(ukeys)} keys against {prov}…"):
                for u in ukeys:
                    ok, msg = self._test_key(prov, u["value"])
                    rows.append([u["masked"], u["src"], "✓ valid" if ok else f"✕ {msg[:40]}"])
            self.ui.table("TEST RESULTS", ["key", "source", "result"], rows,
                          ["accent", "muted", "ok"])
            n_ok = sum(1 for r in rows if r[2].startswith("✓"))
            self.ui.event("ok" if n_ok == len(rows) else "warn",
                          f"{n_ok}/{len(rows)} keys valid")
            return
        if sel.isdigit():
            u = next((x for x in ukeys if x["n"] == int(sel)), None)
            if u:
                with self.ui.spinner(f"testing {u['masked']}…"):
                    ok, msg = self._test_key(prov, u["value"])
                (self.ui.event if ok else self.ui.event)(
                    "ok" if ok else "error", f"{u['masked']}: {'✓ ' + msg if ok else '✕ ' + msg}")
                return
        self.ui.event("warn", "all | number | b — bas yahi options")

    def _test_key(self, prov: str, key: str):
        """Verify a key against that provider's /models endpoint. → (ok, msg)"""
        import json as _json
        import urllib.request
        cfg = self.config.get(f"providers.{prov}", {}) or {}
        base = str(cfg.get("base_url", "")).rstrip("/")
        if not base:
            return False, "base_url missing (config)"
        req = urllib.request.Request(
            f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                n = len(_json.loads(r.read()).get("data", []))
                return True, f"{n} models reachable"
        except Exception as e:
            return False, str(e)[:70]

    cmd_keymanager = cmd_key

    def cmd_keys(self, arg: str = "") -> None:
        if arg.startswith("add "):
            key = arg[4:].strip()
            pname = self.ctx.llm.registry.default_name
            ring = self.ctx.llm.registry.keyrings.get(pname)
            from ..core.keymanager import KeyManager
            KeyManager(self.config).add(pname, key)
            r2 = self.ctx.llm.registry.ensure_provider(pname, [key])
            k = r2.keys[-1] if r2 else None
            if k:
                self.ui.event("ok", f"added {k.label} ({k.masked}) — saved to keys/{pname}.json")
            return
        rows = []
        for prov, keys in self.ctx.llm.key_status().items():
            for k in keys:
                state = k["state"]
                mark = {"healthy": "[ok]● healthy[/]", "cooling": "[warn]◐ cooling[/]",
                        "dead": "[err]✕ dead[/]"}.get(state, state)
                cd = f" {k['cooldown_left']}s" if k["cooldown_left"] else ""
                rows.append([prov, k["label"], k["masked"], mark + cd,
                             k["success"], k["failures"], k["tokens"]])
        if rows:
            self.ui.table("API KEYS", ["provider", "label", "key", "state", "ok", "fail", "tokens"],
                          rows, ["muted", "accent", "muted", "white", "ok", "err", "muted"])
        else:
            self.ui.event("warn", "no API keys configured — set MISTRAL_API_KEY")

    def _persist_key(self, provider: str, key: str) -> None:
        f = self.config.data_dir / "keys.json"
        data = {}
        if f.exists():
            try:
                data = json.loads(f.read_text())
            except Exception:
                data = {}
        data.setdefault(provider, [])
        if key not in data[provider]:
            data[provider].append(key)
        f.write_text(json.dumps(data, indent=2))
        try:
            os.chmod(f, 0o600)
        except Exception:
            pass

    def cmd_skills(self, arg: str = "") -> None:
        lib = self.ctx.skills
        lib.reload()
        if arg:
            found = lib.search(arg, 8)
            if not found:
                self.ui.event("warn", f"no skill matches '{arg}'")
                return
            self.ui.table(f"SKILLS ~ {arg}", ["id", "description"],
                          [[s.id, s.description[:70]] for s in found], ["accent", "muted"])
            return
        rows = [[s.id, s.category, s.description[:60]]
                for s in sorted(lib.skills.values(), key=lambda x: x.id)]
        self.ui.table(f"SKILLS ({len(rows)})", ["id", "category", "description"], rows,
                      ["accent", "agent", "muted"])

    def cmd_skill(self, arg: str) -> None:
        if not arg:
            self.ui.event("warn", "usage: /skill <skill_id>")
            return
        self.ui.answer(self.ctx.skills.load_body(arg, 6000), title=f"SKILL {arg}")

    def cmd_rag(self, _: str = "") -> None:
        if not self.ctx.rag:
            self.ui.event("warn", "RAG disabled")
            return
        st = self.ctx.rag.stats()
        self.ui.table("KNOWLEDGE BASE", ["metric", "value"],
                      [["chunks", st["chunks"]], ["sources", st["sources"]]], ["muted", "accent"])
        if st["top_sources"]:
            self.ui.table("TOP SOURCES", ["source", "chunks"],
                          [[Path(s).name if "://" not in s else s, n]
                           for s, n in st["top_sources"]], ["white", "accent"])

    def cmd_index(self, arg: str) -> None:
        if not self.ctx.rag:
            self.ui.event("warn", "RAG disabled")
            return
        target = Path(arg or ".")
        if not target.is_absolute():
            target = self.config.workspace / target
        if not target.exists():
            self.ui.event("error", f"not found: {target}")
            return
        with self.ui.progress() as prog:
            tid = prog.add_task("indexing", total=100)

            def cb(name: str, i: int, total: int) -> None:
                prog.update(tid, completed=int(i / max(total, 1) * 100), description=name[:26])

            if target.is_dir():
                st = self.ctx.rag.index_directory(target, force=True, progress=cb)
            else:
                n = self.ctx.rag.index_file(target, force=True)
                st = {"files_indexed": 1 if n else 0, "chunks": n}
        self.ui.event("ok", f"indexed: {st}")

    def cmd_forget_index(self, _: str = "") -> None:
        if self.ctx.rag and self.ui.confirm("Clear the whole RAG index?", False):
            n = self.ctx.rag.clear()
            self.ui.event("ok", f"cleared {n} chunks")

    def cmd_memory(self, _: str = "") -> None:
        if not self.ctx.memory:
            self.ui.event("warn", "memory disabled")
            return
        st = self.ctx.memory.stats()
        self.ui.table("MEMORY", ["metric", "value"], [[k, v] for k, v in st.items()],
                      ["muted", "accent"])
        facts = self.ctx.memory.recall(limit=15)
        if facts:
            self.ui.table("FACTS", ["kind", "key", "value"],
                          [[f["kind"], f["key"], str(f["value"])[:50]] for f in facts],
                          ["agent", "accent", "muted"])

    def cmd_remember(self, arg: str) -> None:
        if "=" not in arg:
            self.ui.event("warn", "usage: /remember key=value")
            return
        k, v = arg.split("=", 1)
        if self.ctx.memory:
            self.ctx.memory.remember("preference", k.strip(), v.strip(), 0.9)
            self.ui.event("ok", f"remembered {k.strip()}")

    def cmd_sessions(self, _: str = "") -> None:
        if not self.ctx.memory:
            return
        sessions = self.ctx.memory.list_sessions(15)
        if not sessions:
            self.ui.print("  [muted]no sessions yet[/]")
            return
        rows = [[str(i + 1), s["id"][:10],
                 time.strftime("%m-%d %H:%M", time.localtime(s["created"])),
                 str(s["msgs"]), (s["goal"] or s["title"])[:44]]
                for i, s in enumerate(sessions)]
        self.ui.table("SESSIONS  (/resume <number or id>)",
                      ["#", "id", "when", "msgs", "goal"], rows,
                      ["accent", "muted", "muted", "muted", "white"])

    def cmd_resume(self, arg: str) -> None:
        if not self.ctx.memory:
            return
        ref = arg.strip()
        if not ref:
            # bare /resume → resume the most recent session
            ref = self.ctx.memory.latest_session() or ""
            if not ref:
                self.ui.event("error", "no sessions to resume yet")
                return
            sid = ref
        else:
            sid = self.ctx.memory.resolve_session(ref)
        if sid and self.ctx.memory.resume_session(sid):
            goal = ""
            for s in self.ctx.memory.list_sessions(50):
                if s["id"] == sid:
                    goal = (s["goal"] or s["title"])[:60]
                    break
            self.ui.event("ok", f"resumed {sid[:10]} — {goal}")
        else:
            self.ui.event("error", f"session not found: {ref!r} "
                          "(see /sessions for numbers and ids)")

    def cmd_tools(self, _: str = "") -> None:
        rows = [[t.name, t.risk.value, ("all" if "*" in t.agents else ",".join(t.agents))[:28],
                 t.description[:52]] for t in sorted(self.ctx.tools.all(), key=lambda x: x.name)]
        self.ui.table(f"TOOLS ({len(rows)})", ["name", "risk", "agents", "description"], rows,
                      ["accent", "warn", "agent", "muted"])

    def cmd_plan(self, arg: str) -> None:
        if not arg:
            self.ui.event("warn", "usage: /plan <goal>")
            return
        from ..orchestrator.dag import TaskDAG
        with self.ui.spinner("planning…"):
            plan = self.orchestrator.supervisor.plan(arg)
        self.ui.show_plan(plan, TaskDAG.from_plan(plan))
        if self.ui.confirm("Execute this plan?", True):
            self.run_goal(arg, force=True)

    def cmd_auto(self, arg: str) -> None:
        if arg:
            self.run_goal(arg, force=True)

    def cmd_agent(self, arg: str) -> None:
        parts = arg.split(" ", 1)
        if len(parts) < 2:
            self.ui.event("warn", "usage: /agent <researcher|worker|coder|critic> <task>")
            return
        name, task = parts[0].lower(), parts[1]
        agent = self.orchestrator.agent_for(name)
        self.ui.phase("AGENT", f"{name} running solo")
        out = agent.run(task, on_step=lambda s: self.ui.task_step(type("T", (), {"id": name})(), s))
        self.ui.answer(out.output, title=f"{name.upper()} RESULT")
        self.ui.print(f"[muted]{len(out.steps)} steps · {out.tokens} tokens · "
                      f"{out.elapsed:.1f}s · {out.model}[/]")

    def cmd_projects(self, _: str = "") -> None:
        ws = self.config.workspace
        dirs = [d for d in sorted(ws.iterdir()) if d.is_dir()]
        rows = []
        for d in dirs:
            n = sum(1 for _ in d.rglob("*") if _.is_file())
            if n or d.name == "projects":
                rows.append([d.name, n, str(d)])
        if rows:
            self.ui.table("WORKSPACE PROJECTS", ["folder", "files", "path"], rows,
                          ["accent", "muted", "muted"])
        else:
            self.ui.event("info", "workspace empty — build goals create projects/ folders")

    def cmd_cd(self, arg: str) -> None:
        p = Path(arg).expanduser()
        if not p.is_absolute():
            p = (self.config.workspace / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
        self.config.set("app.workspace", str(p))
        self.ctx = AgentContext(self.config, self.ui, self.ui.notify)
        self.ctx.approval_handler = self._approval
        self.orchestrator = Orchestrator(self.ctx)
        self.ui.event("ok", f"workspace = {p}")

    def cmd_mode(self, arg: str) -> None:
        if arg in ("smart", "always", "never"):
            self.config.set("safety.approval_mode", arg)
            self.ui.event("ok", f"approval mode = {arg}"
                                + (" [warn](YOLO — no confirmations!)[/]" if arg == "never" else ""))
        else:
            self.ui.event("warn", "usage: /mode smart|always|never")


# ======================================================================
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="nexus", description="Nexus — autonomous CLI agent")
    ap.add_argument("goal", nargs="*", help="goal to run (one-shot mode)")
    ap.add_argument("-c", "--config", help="path to config.yaml")
    ap.add_argument("-w", "--workspace", help="workspace directory")
    ap.add_argument("-t", "--theme", choices=["cyber", "matrix", "mono"])
    ap.add_argument("-m", "--mode", choices=["smart", "always", "never"],
                    help="approval mode (never = full autonomy)")
    ap.add_argument("-q", "--quiet", action="store_true", help="less step output")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        from ..core.config import get_config as _gc
        print(f"nexus-agent {_gc().get('app.version', '1.8.7')}")
        return 0

    app = NexusApp(args.config, args.theme, not args.quiet, args.mode, args.workspace)
    if args.goal:
        app.start()
        goal = " ".join(args.goal)
        app.ui.user_echo(goal)
        app.run_goal(goal)
        return 0
    app.repl()
    return 0


if __name__ == "__main__":
    sys.exit(main())
