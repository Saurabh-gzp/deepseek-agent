"""Shell + Python execution tools (Termux-aware, guarded)."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from .base import Risk, ToolRegistry, ToolResult

DANGEROUS = [
    r"rm\s+-rf\s+/(?!\w)", r"rm\s+-rf\s+~", r"mkfs", r"dd\s+if=/dev/(zero|random)\s+of=/dev/",
    r":\(\)\{.*\};:", r">\s*/dev/sd", r"chmod\s+-R\s+777\s+/", r"shutdown", r"reboot",
    r"pkill\s+-9\s+-u", r"curl[^|]*\|\s*(sudo\s+)?(bash|sh)", r"wget[^|]*\|\s*(sudo\s+)?(bash|sh)",
    r"git\s+push\s+.*--force.*\s+(main|master)", r"npm\s+publish", r"pip\s+.*--break-system-packages\s+.*sudo",
]

# --- deletion choke-point ------------------------------------------------
# Deletion has exactly ONE path: the `delete_path` tool (which asks for human
# approval). Any file-deletion attempt inside run_shell / run_python == hard block.
# In live adversarial tests the agent tried all of these routes:
#   rm, shred, find -delete, python -c os.remove, os.system('rm ...'), .trash move
SHELL_DELETE = re.compile(
    r"(^|[\s;&|(])(rm|rmdir|unlink|shred|srm|wipe|trash-put|trash)\b"
    r"|(^|\s)-delete\b"
    r"|find\s+[^;|]*-delete"
    r"|python[23]?\s+-c\s+.*(os\.remove|os\.unlink|shutil\.rmtree|os\.rmdir)"
    r"|os\.(remove|unlink|rmdir)\s*\("
    r"|shutil\.rmtree"
    r"|mv\s+\S+\s+\S*\.(trash|deleted)", re.IGNORECASE)

PY_DELETE_API = ("os.remove", "os.unlink", "os.rmdir", "shutil.rmtree",
                 "os.system", "subprocess", "os.popen", "os.spawn", "pathlib.Path.unlink")
PY_DELETE_RX = re.compile(
    r"os\.(remove|unlink|rmdir|renames?)\s*\("
    r"|\.unlink\s*\("
    r"|shutil\.rmtree\s*\("
    r"|os\.system\s*\("
    r"|os\.popen\s*\("
    r"|subprocess\.(run|call|check_output|Popen)\s*\("
    r"|\bos\.spawn\w*\s*\("
    r"|(^|[^a-z])rm\s+-?[a-z]*\s+\S", re.IGNORECASE)

DELETE_GUIDE = ("STOP attempting shell/python deletions — they are ALWAYS blocked. "
                "The ONLY way: call delete_path(path=...) — it asks the user once, "
                "and on 'always' the whole delete batch proceeds without re-asking.")


class ShellTools:
    def __init__(self, workspace: Path, timeout: int = 120,
                 blocked_patterns: Optional[List[str]] = None, approval_cb=None):
        self.root = Path(workspace).resolve()
        self.timeout = timeout
        self.blocked = [re.compile(p, re.IGNORECASE) for p in DANGEROUS]
        for p in (blocked_patterns or []):
            self.blocked.append(re.compile(re.escape(p), re.IGNORECASE))
        self.approval_cb = approval_cb
        self.is_termux = "com.termux" in os.environ.get("PREFIX", "")

    # ------------------------------------------------------------------
    def is_dangerous(self, cmd: str) -> Optional[str]:
        for rx in self.blocked:
            if rx.search(cmd):
                return rx.pattern
        return None

    def run_shell(self, command: str, cwd: str = ".", timeout: int = 0) -> ToolResult:
        # ---- deletion choke-point: rm/shred/find -delete etc. hard-blocked
        if SHELL_DELETE.search(command):
            return ToolResult(False, error="BLOCKED: this command deletes files. " + DELETE_GUIDE)
        danger = self.is_dangerous(command)
        if danger:
            if self.approval_cb is None:
                return ToolResult(False, error=f"BLOCKED dangerous command (pattern: {danger}). "
                                               "Ask the user to run it manually.")
            if not self.approval_cb("shell_dangerous", command):
                return ToolResult(False, error="User denied dangerous command.")
        wd = (self.root / cwd).resolve() if not Path(cwd).is_absolute() else Path(cwd)
        if not str(wd).startswith(str(self.root)):
            wd = self.root
        wd.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(wd), capture_output=True, text=True,
                timeout=timeout or self.timeout,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb", "CI": "1"},
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            body = f"$ {command}\n(exit {proc.returncode})"
            if out:
                body += f"\n--- stdout ---\n{out[:8000]}"
            if err:
                body += f"\n--- stderr ---\n{err[:4000]}"
            return ToolResult(proc.returncode == 0, output=body,
                              error="" if proc.returncode == 0 else f"exit code {proc.returncode}",
                              data={"code": proc.returncode, "stdout": out, "stderr": err})
        except subprocess.TimeoutExpired:
            return ToolResult(False, error=f"Command timed out after {timeout or self.timeout}s: {command}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=str(e))

    def run_python(self, code: str, timeout: int = 0) -> ToolResult:
        """Run python snippet in a temp file inside the workspace."""
        # ---- deletion choke-point: os.remove/unlink/rmtree/os.system/
        #      subprocess waghera se file deletion = hard block
        if PY_DELETE_RX.search(code):
            return ToolResult(False, error="BLOCKED: this code deletes files (os.remove/"
                                          "unlink/rmtree/subprocess/os.system). " + DELETE_GUIDE)
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             dir=str(self.root), encoding="utf-8") as f:
                f.write(code)
                tmp = f.name
            proc = subprocess.run([sys.executable, tmp], capture_output=True, text=True,
                                  timeout=timeout or self.timeout, cwd=str(self.root))
            os.unlink(tmp)
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            body = out[:8000] or "(no stdout)"
            if err:
                body += f"\n--- stderr ---\n{err[:4000]}"
            return ToolResult(proc.returncode == 0, output=body,
                              error="" if proc.returncode == 0 else err[:500])
        except subprocess.TimeoutExpired:
            return ToolResult(False, error="Python execution timed out")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=str(e))

    def install_package(self, package: str, manager: str = "pip") -> ToolResult:
        safe = re.match(r"^[A-Za-z0-9._\-\[\]=<>,+ ]+$", package)
        if not safe:
            return ToolResult(False, error="Invalid package name")
        cmds = {
            "pip": f"{sys.executable} -m pip install --quiet {package}",
            "npm": f"npm install {package}",
            "pkg": f"pkg install -y {package}",
            "apt": f"apt-get install -y {package}",
        }
        cmd = cmds.get(manager)
        if not cmd:
            return ToolResult(False, error=f"Unknown manager '{manager}'")
        return self.run_shell(cmd, timeout=420)

    def system_info(self) -> ToolResult:
        import platform
        info = [
            f"platform : {platform.platform()}",
            f"python   : {platform.python_version()}",
            f"machine  : {platform.machine()}",
            f"termux   : {self.is_termux}",
            f"workspace: {self.root}",
            f"cwd files: {len(list(self.root.iterdir()))}",
        ]
        for tool in ("git", "node", "npm", "curl", "ffmpeg"):
            p = subprocess.run(f"command -v {tool}", shell=True, capture_output=True, text=True)
            info.append(f"{tool:9}: {'yes' if p.returncode == 0 else 'no'}")
        info += self._device_probes()
        return ToolResult(True, output="\n".join(info))

    def _device_probes(self) -> List[str]:
        """Battery/storage/memory/network — Termux termux-api + Linux /sys fallbacks.
        This exists so the agent never lies about 'no access' — for device
        questions it runs these probes (read-only, safe)."""
        out: List[str] = []

        def probe(label: str, cmd: str) -> None:
            try:
                p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
                txt = (p.stdout or p.stderr or "").strip()
                if txt:
                    out.append(f"{label}: {txt[:300]}")
            except Exception:
                pass

        probe("battery(termux)", "termux-battery-status 2>/dev/null")
        if not any(x.startswith("battery(termux)") for x in out):
            # Linux fallback: power supply sysfs
            try:
                base = Path("/sys/class/power_supply")
                if base.exists():
                    for bat in sorted(base.iterdir()):
                        if bat.name.startswith(("BAT", "bat")):
                            cap = (bat / "capacity").read_text().strip()
                            st = (bat / "status").read_text().strip()
                            out.append(f"battery: {cap}% ({st}) — {bat.name}")
                            break
            except Exception:
                pass
        probe("storage", "df -h . 2>/dev/null | tail -1")
        probe("memory", "free -m 2>/dev/null | sed -n '2p' || cat /proc/meminfo | head -3")
        probe("network ip", "ip -4 addr show 2>/dev/null | grep inet | grep -v 127.0.0.1 | head -3")
        probe("uptime", "uptime 2>/dev/null")
        probe("termux-api cmds", "ls $PREFIX/bin 2>/dev/null | grep '^termux-' | tr '\\n' ' ' | head -c 300")
        return out

    # ------------------------------------------------------------------
    def register(self, reg: ToolRegistry) -> None:
        S = {"type": "string"}
        I = {"type": "integer"}
        reg.add("run_shell",
                "Execute a shell command in the workspace (tests, git, build, ls...). "
                "Prefer non-interactive flags. Long/interactive commands will time out.",
                {"type": "object", "properties": {"command": S, "cwd": S, "timeout": I},
                 "required": ["command"]},
                self.run_shell, Risk.EXECUTE,
                agents=["supervisor", "coder", "worker", "critic", "solo"])
        reg.add("run_python", "Run a Python code snippet and capture stdout (calculations, data work).",
                {"type": "object", "properties": {"code": S, "timeout": I}, "required": ["code"]},
                self.run_python, Risk.EXECUTE,
                agents=["supervisor", "coder", "worker", "researcher", "critic", "solo"])
        reg.add("install_package", "Install a dependency (pip/npm/pkg/apt).",
                {"type": "object", "properties": {
                    "package": S, "manager": {"type": "string", "enum": ["pip", "npm", "pkg", "apt"]}},
                 "required": ["package"]},
                self.install_package, Risk.EXECUTE, agents=["coder", "supervisor", "solo"])
        reg.add("system_info", "Show OS/python/tool availability of the host device.",
                {"type": "object", "properties": {}}, self.system_info, Risk.READ_ONLY)
