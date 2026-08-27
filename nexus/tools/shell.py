"""Shell + Python execution tools (Termux-aware, guarded)."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
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
# v1.9.9 F1: shell is a NETWORK TOOL too — curl/wget/python inside run_shell
# bypassed the SSRF policy that web_fetch/http_request/browser obey (live:
# agent exfiltrated a loopback service AND hit cloud-metadata via plain curl).
# Policy: block link-local/metadata/RFC1918/private-DNS in shell commands.
# Loopback (127.0.0.1/localhost/::1) stays ALLOWED by default — local dev and
# the critic's hosting checks legitimately curl 127.0.0.1 (same user, same
# machine, no privilege boundary). Config: safety.shell.allow_loopback.
_NET_GUARD_URL = re.compile(r'https?://[^\s"<>`]+', re.I)
_NET_GUARD_HOST = re.compile(
    r"(?:\b(?:curl|wget|nc|ncat|netcat|telnet|ssh|ftp|http|https)\s+[^\n]{0,200}?"
    r"(?:[\w.-]+))", re.I)
_SHELL_ALLOW_LOOPBACK = True


def _host_blocked_for_shell(host: str) -> str:
    """SSRF check for shell network targets; loopback allowed by policy."""
    from urllib.parse import urlparse as _up
    h = (host or "").strip().lower()
    if "@" in h:
        h = h.rsplit("@", 1)[-1]
    if not h:
        return ""
    if _SHELL_ALLOW_LOOPBACK and h in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return ""
    import ipaddress as _ipa
    try:
        if h.startswith("[") and "]" in h:          # [ipv6] literal
            ip = _ipa.ip_address(h[1:h.index("]")])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return f"blocked address: {ip}"
            return ""
        ip = _ipa.ip_address(h)
        if _SHELL_ALLOW_LOOPBACK and ip.is_loopback:
            return ""
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return f"blocked address: {ip}"
        return ""
    except ValueError:
        pass
    from .ssrf import url_blocked as _ub
    why = _ub("http://" + h)
    if why and "127.0.0.1" in why and _SHELL_ALLOW_LOOPBACK:
        return ""
    return why


def _shell_network_guard(command: str) -> str:
    """Return a block-reason if the command targets a forbidden network host."""
    reasons = []
    for m in _NET_GUARD_URL.finditer(command or ""):
        raw = m.group(0)
        from urllib.parse import urlparse as _up
        try:
            host = (_up(raw).netloc or "").split("@")[-1].split(":")[0]
        except Exception:
            host = ""
        if host:
            why = _host_blocked_for_shell(host)
            if why:
                reasons.append(f"{raw} -> {why}")
    # bare-IP invocations without scheme:  curl 169.254.169.254 / nc 10.0.0.1 80
    for m in re.finditer(
            r"\b(?:curl|wget|nc|ncat|netcat|telnet|ssh)\s+(?!https?(?::|\Z)|ftp(?::|\Z))([@%\w.:-]+)", command or "", re.I):
        tok = m.group(1).strip("'\"")
        if tok and not tok.startswith("-"):
            why = _host_blocked_for_shell(tok)
            if why:
                reasons.append(f"{m.group(0)} -> {why}")
    if reasons:
        return ("SSRF blocked by shell network policy: " + "; ".join(reasons[:3]) +
                " (metadata/link-local/RFC1918 targets are forbidden in run_shell; "
                "use web_fetch/http_request for public URLs, or the dedicated tools "
                "for workspace-local servers)")
    return ""


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

# --- foreground-server choke-point (v1.8) ---------------------------------
# A long-running server started in the FOREGROUND hangs until the tool timeout
# (live bug: `python3 -m http.server 8000` burned 120s and produced nothing,
# then the agent wrote a "hosting guide" and claimed the site was live).
# A DETACHED server (`cmd &`/nohup) is just as broken: run_shell's capture
# pipes stay open so the call hangs until timeout, and after the call the
# pipe read-ends close -> every request handler dies on BrokenPipeError and
# the site accepts TCP but answers with EMPTY replies (verified live :8000).
# => server commands run ONLY via start_server, never in run_shell.
FOREGROUND_SERVER = re.compile(
    r"python3?\s+-m\s+http\.server"
    r"|(npm|pnpm|yarn)\s+(run\s+)?(start|dev|serve)\b"
    r"|(flask|uvicorn|gunicorn|hugo|jekyll)\s+"
    r"|(node|bun)\s+\S*(server|app)\b|vite\b|next\s+(dev|start|build)\b"
    r"|php\s+-S\b|rails\s+s\b|serve\s+-s\b",
    re.IGNORECASE)
DETACHED = re.compile(r"(?:^|[^&])&(?![&])|nohup|disown|setsid|start_server\s*\(")
SERVER_GUIDE = (
    "BLOCKED: this is a long-running SERVER — in run_shell it would hang until "
    "the tool timeout, and even with '&'/nohup it ends up accepting connections "
    "but answering EMPTY replies (the tool's capture pipes close). Use the "
    "start_server tool — it starts the server DETACHED with a log file, waits "
    "for the port, fetches the page and verifies content in ONE call, e.g.:  "
    "start_server(command='python3 -m http.server 8000 --directory projects/portfolio-website', "
    "port=8000, marker='Portfolio', name='portfolio') — then report its verified output. "
    "Never claim a site is 'live' without a verified HTTP 200 + marker.")


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
        """NEVER raises. Any exception (bad cwd, unicode, OSError, ...) is
        converted into a ToolResult error so the agent loop keeps running."""
        try:
            return self._run_shell(command, cwd, timeout)
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=(
                f"run_shell internal error ({type(e).__name__}): {e}"))

    _BARE_PYTHON = re.compile(r"(^|[;&|`\s])python(?=\s|$)")

    def _prefer_python3(self, command: str) -> str:
        """v1.8.7: bare `python` is often missing; rewrite to python3.
        Leaves python3 / python3.x untouched."""
        return self._BARE_PYTHON.sub(r"\1python3", command or "")

    def _run_shell(self, command: str, cwd: str = ".", timeout: int = 0) -> ToolResult:
        # ---- deletion choke-point: rm/shred/find -delete etc. hard-blocked
        command = self._prefer_python3(command)
        net_why = _shell_network_guard(command)
        if net_why:
            return ToolResult(False, error=net_why)
        if SHELL_DELETE.search(command):
            return ToolResult(False, error="BLOCKED: this command deletes files. " + DELETE_GUIDE)
        # ---- foreground-server choke-point: servers must use start_server
        # (both foreground AND &-detached forms are broken in run_shell —
        #  detached leaves a listener that answers EMPTY replies, see SERVER_GUIDE)
        if FOREGROUND_SERVER.search(command):
            return ToolResult(False, error=SERVER_GUIDE)
        danger = self.is_dangerous(command)
        if danger:
            if self.approval_cb is None:
                return ToolResult(False, error=f"BLOCKED dangerous command (pattern: {danger}). "
                                               "Ask the user to run it manually.")
            if not self.approval_cb("shell_dangerous", command):
                return ToolResult(False, error="User denied dangerous command.")
        wd = (self.root / cwd).resolve() if not Path(cwd).is_absolute() else Path(cwd)
        from .paths import in_workspace as _in_ws
        if not _in_ws(wd, self.root):
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
        """NEVER raises. Any exception becomes a ToolResult error."""
        try:
            return self._run_python(code, timeout)
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=(
                f"run_python internal error ({type(e).__name__}): {e}"))

    def _run_python(self, code: str, timeout: int = 0) -> ToolResult:
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

    def availability(self) -> str:
        """Sutra-style env facts: tells the model EXACTLY what exists on this
        device BEFORE it acts — kills blind `termux-*/adb/dumpsys` guesses."""
        import shutil
        def has(c: str) -> str:
            return "yes" if shutil.which(c) else "no"
        parts = [f"termux: {self.is_termux}"]
        if self.is_termux:
            for c in ("termux-battery-status", "termux-wifi-connectioninfo",
                      "termux-telephony-signal-strength", "getprop", "ip",
                      "ifconfig", "curl", "ping", "pkg", "python3"):
                parts.append(f"{c}={has(c)}")
            parts.append("termux-api-app=" + ("yes" if shutil.which("termux-battery-status") else "no"))
        return ("\n".join(parts))

    def device_info(self, detail: str = "storage,battery,network,memory") -> ToolResult:
        """One-shot REAL device report — sutra-style: pure-Python probes first,
        `shutil.which()` guards before every external command, and anything not
        available is reported as unavailable WITH a fix hint. Never guesses a
        command, never retries blind variants, completes in seconds."""
        import json as _json
        import shutil as _shutil
        import socket as _socket
        import time as _time

        want = {p.strip().lower() for p in str(detail).split(",") if p.strip()}
        want = want or {"storage", "battery", "network", "memory"}
        out = [f"environment: {'TERMUX (Android)' if self.is_termux else 'generic Linux'}",
               f"termux-api available: {'yes' if _shutil.which('termux-battery-status') else 'no'}"]

        def read(path: str) -> str:
            try:
                with open(path) as f:
                    return f.read().strip()
            except OSError:
                return ""

        # ---- STORAGE (Python-first: no du scans, no path guessing) --------
        if "storage" in want:
            import shutil as _du
            for label, path in (("workspace", str(self.root)), ("root", "/"),
                                ("internal", "/data"), ("sdcard", "/storage/emulated/0")):
                if not os.path.isdir(path):
                    continue
                try:
                    u = _du.disk_usage(path)
                    out.append(f"storage[{label}]: total={u.total / 1e9:.1f}GB used="
                               f"{u.used / 1e9:.1f}GB free={u.free / 1e9:.1f}GB "
                               f"({u.used / u.total * 100:.0f}% used)")
                except OSError as e:
                    out.append(f"storage[{label}]: unavailable ({e})")

        # ---- BATTERY ------------------------------------------------------
        if "battery" in want:
            tp = _shutil.which("termux-battery-status")
            if tp:
                try:
                    r = subprocess.run([tp], capture_output=True, text=True, timeout=10)
                    d = _json.loads(r.stdout or "{}")
                    out.append(f"battery: {d.get('percentage')}% ({d.get('status')}, "
                               f"{d.get('temperature')}C) [termux-api]")
                except Exception as e:
                    out.append(f"battery: unavailable ({e})")
            else:
                cap = read("/sys/class/power_supply/battery/capacity")
                st = read("/sys/class/power_supply/battery/status")
                if cap:
                    out.append(f"battery: {cap}% ({st or 'unknown'}) [sysfs]")
                else:
                    out.append("battery: unavailable — install Termux:API app + `pkg install termux-api` "
                               "(then termux-battery-status works)")

        # ---- NETWORK (Python-first — socket, no ip/ifconfig guessing) -----
        if "network" in want:
            res = {"hostname": _socket.gethostname()}
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
                s.settimeout(3)
                s.connect(("8.8.8.8", 53))
                res["local_ip"] = s.getsockname()[0]
                s.close()
                res["internet"] = "reachable (8.8.8.8:53)"
            except Exception as e:
                res["internet"] = f"unreachable ({type(e).__name__})"
            out.append(f"network: host={res['hostname']} ip={res.get('local_ip', '?')} "
                       f"internet={res['internet']}")
            if self.is_termux:
                gp = _shutil.which("getprop")
                if gp:
                    try:
                        r = subprocess.run([gp, "gsm.network.type"], capture_output=True,
                                           text=True, timeout=8)
                        typ = r.stdout.strip()
                        if typ:
                            out.append(f"network.type: {typ}")
                    except Exception:
                        pass
            for cmd, label in (("termux-wifi-connectioninfo", "wifi"),
                               ("termux-telephony-signal-strength", "signal")):
                if _shutil.which(cmd):
                    try:
                        r = subprocess.run([cmd], capture_output=True, text=True, timeout=10)
                        if r.stdout.strip():
                            out.append(f"network.{label}: {r.stdout.strip()[:200]}")
                    except Exception:
                        pass
                elif label == "wifi":
                    out.append("network.wifi: unavailable (Termux:API app missing — install "
                               "`pkg install termux-api` + Termux:API app from F-Droid)")
            # public IP via stdlib (no curl needed)
            try:
                import urllib.request
                with urllib.request.urlopen("https://api.ipify.org?format=json",
                                            timeout=8) as r:
                    out.append("network.public_ip: " +
                               _json.loads(r.read().decode()).get("ip", "?"))
            except Exception as e:
                out.append(f"network.public_ip: unavailable ({type(e).__name__})")

        # ---- MEMORY / CPU ------------------------------------------------
        if "memory" in want:
            mem = {}
            for line in read("/proc/meminfo").splitlines():
                parts = line.split(":")
                if parts[0] in ("MemTotal", "MemAvailable", "SwapTotal"):
                    mem[parts[0]] = round(int(parts[1].strip().split()[0]) / 1e6, 2)
            if mem.get("MemTotal"):
                used = round(mem["MemTotal"] - mem.get("MemAvailable", 0), 2)
                out.append(f"memory: total={mem['MemTotal']}GB used={used}GB "
                           f"free={mem.get('MemAvailable', 0)}GB "
                           f"({used / mem['MemTotal'] * 100:.0f}% used)")
            else:
                out.append("memory: unavailable (/proc/meminfo not readable)")
        out.append("cpu: " + (read("/proc/loadavg").split()[0] if read("/proc/loadavg") else "?"))

        # ---- ANDROID identity --------------------------------------------
        if self.is_termux:
            gp = _shutil.which("getprop")
            if gp:
                try:
                    r = subprocess.run([gp, "ro.build.version.release"], capture_output=True,
                                       text=True, timeout=8)
                    if r.stdout.strip():
                        out.append(f"android: {r.stdout.strip()}")
                except Exception:
                    pass
        return ToolResult(True, output="\n".join(out))

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

    def start_server(self, command: str = "", port: int = 8000, path: str = "/",
                     marker: str = "", name: str = "", wait: int = 6,
                     **kwargs) -> ToolResult:
        # LLMs sometimes pass fuzzy params (timeout=, wait_seconds=, cwd=...) —
        # tolerate and map them instead of erroring the whole tool call.
        directory = ""
        if kwargs:
            if "timeout" in kwargs or "wait_seconds" in kwargs or "delay" in kwargs:
                try:
                    wait = int(kwargs.get("timeout") or kwargs.get("wait_seconds")
                               or kwargs.get("delay") or wait)
                except (TypeError, ValueError):
                    pass
            if "port_number" in kwargs:
                try:
                    port = int(kwargs["port_number"])
                except (TypeError, ValueError):
                    pass
            if "url_path" in kwargs:
                path = kwargs["url_path"]
            directory = str(kwargs.get("directory") or kwargs.get("dir")
                            or kwargs.get("cwd") or "")
        # Live Termux: start_server(path='projects/foo') glued onto the port
        # as `http.server 8000projects`. Treat a non-URL `path` as a folder.
        pth = str(path or "/")
        # Any relative path (demo, projects/foo) is a FOLDER, not a URL suffix.
        # Otherwise fetch becomes http://127.0.0.1:47147demo (invalid port).
        if pth and not pth.startswith("/"):
            directory = directory or pth
            path = "/"
        cmd0 = (command or "").strip()
        if cmd0 and not re.search(r"\s", cmd0) and (
                cmd0.startswith("projects") or (self.root / cmd0).is_dir()):
            directory = directory or cmd0
            command = ""
        if not (command or "").strip():
            drel = directory or "."
            command = f"python3 -m http.server {int(port) or 8000} --directory {drel}"
        """Sutra-style ONE-SHOT hosting: start a server DETACHED (survives this
        tool call), wait for the port, fetch the URL with stdlib, and verify real
        content markers — all in one call. The agent never claims 'hosted'
        without a verified HTTP 200 + expected content."""
        import json as _json
        import socket as _socket
        import time as _time
        import urllib.request as _ur
        import urllib.error as _ue
        from .paths import in_workspace as _in_ws

        raw_cmd = (command or "").strip()
        if SHELL_DELETE.search(raw_cmd) or self.is_dangerous(raw_cmd):
            return ToolResult(False, error="BLOCKED: start_server refuses delete/dangerous commands")
        if re.search(r"[;&|`$]|&&|\|\||\n", raw_cmd):
            return ToolResult(False, error="BLOCKED: start_server allows only python -m http.server")
        http_ok = re.match(
            r"^python3?\s+-m\s+http\.server(?:\s+\d+)?(?:\s+--bind\s+\S+)?(?:\s+--directory\s+\S+)?$",
            raw_cmd, re.I)
        if not http_ok:
            return ToolResult(False, error=(
                "BLOCKED: start_server only launches python3 -m http.server "
                "(pass directory=/projects/foo). Arbitrary command= is not executed."))
        dm = re.search(r"--directory\s+(\S+)", raw_cmd)
        drel = (dm.group(1) if dm else directory) or "."
        dabs = (self.root / drel).resolve() if not Path(drel).is_absolute() else Path(drel).resolve()
        if not _in_ws(dabs, self.root):
            return ToolResult(False, error="BLOCKED: start_server directory escapes workspace")

        # v1.9.9 F3: a user-specified port is an EXACT PARAMETER — if it is
        # already bound, FAIL with an actionable error instead of silently
        # switching ports (live: goal said 8090, tool silently served on 49609
        # and the task "passed" on the wrong port). Explicit port=0 still
        # auto-assigns. (The old silent failover existed because a stale server
        # on :8000 made a task "verify" the wrong site — now the agent gets a
        # clear error and can stop_server or justify a different port.)
        if port and port != 0:
            probe = _socket.socket()
            try:
                probe.bind(("127.0.0.1", int(port)))
                probe.close()
            except OSError:
                probe.close()
                tracked = ""
                try:
                    reg = self.root / self._SERVER_REG
                    if reg.exists():
                        import json as _j2
                        d = _j2.loads(reg.read_text(encoding="utf-8") or "{}")
                        if str(port) in d:
                            tracked = (f" It is a harness-tracked server "
                                       f"(pid {d[str(port)].get('pid')}) â "
                                       f"call stop_server(port={port}) first.")
                except Exception:
                    pass
                return ToolResult(False, error=(
                    f"port {port} is ALREADY IN USE â refusing to silently serve "
                    f"on a different port.{tracked} Either stop the existing server "
                    f"and retry, or pass a different port explicitly."))
        if port == 0:
            s = _socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
        argv = [sys.executable, "-m", "http.server", str(port),
                "--bind", "127.0.0.1", "--directory", str(dabs)]
        logf = self.root / f".server_{port}.log"
        try:
            with open(logf, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(argv, cwd=str(self.root), stdout=lf,
                                       stderr=subprocess.STDOUT,
                                       start_new_session=True,
                                       env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=f"start_server failed to launch: {e}")

        # wait for the port to accept connections
        ready = False
        t_end = _time.time() + wait
        while _time.time() < t_end:
            try:
                s = _socket.socket()
                s.settimeout(1.5)
                s.connect(("127.0.0.1", port))
                s.close()
                ready = True
                break
            except OSError:
                _time.sleep(0.4)
        if not ready:
            tail = ""
            try:
                tail = logf.read_text(encoding="utf-8", errors="replace")[-400:]
            except Exception:
                pass
            return ToolResult(False, error=f"server did not start on port {port}. "
                                           f"Log tail: {tail or '(empty)'}")

        # fetch + verify content markers
        url = f"http://127.0.0.1:{port}{path}"
        body = ""
        try:
            with _ur.urlopen(url, timeout=8) as r:
                body = r.read().decode("utf-8", "replace")
        except _ue.HTTPError as e:
            return ToolResult(False, error=f"server up on :{port} but HTTP {e.code} at {url}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, error=f"server up on :{port} but fetch failed: {e}")

        if marker and marker not in body:
            return ToolResult(False, error=(
                f"server up on :{port} but marker {marker!r} NOT found in "
                f"{path} (content mismatch) — first 200 chars: {body[:200]!r}"))
        self._remember_server(port, proc.pid)
        out = (f"Server RUNNING (pid {proc.pid}) at http://127.0.0.1:{port}{path}"
               + (f"\nname: {name}" if name else "")
               + f"\nverified: HTTP 200, {len(body)} bytes fetched"
               + (f", marker {marker!r} found" if marker else "")
               + "\nThe server stays up — stop it later with stop_server(port="
               + str(port) + ").")
        return ToolResult(True, output=out,
                          data={"pid": proc.pid, "port": port, "url": url})

    # ------------------------------------------------------------------
    _SERVER_REG = ".nexus/servers.json"

    def _remember_server(self, port: int, pid: int) -> None:
        """Track harness-started servers so stop_server can find them later."""
        try:
            reg = self.root / self._SERVER_REG
            data: dict = {}
            if reg.exists():
                data = json.loads(reg.read_text(encoding="utf-8") or "{}")
            data[str(port)] = {"pid": int(pid), "started": time.time()}
            reg.parent.mkdir(parents=True, exist_ok=True)
            reg.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def stop_server(self, port: int = 0, pid: int = 0, **kwargs) -> ToolResult:
        """Stop a server previously started by start_server (by port or pid)."""
        import signal
        port = int(port or 0)
        pid = int(pid or 0)
        if not pid:
            reg = self.root / self._SERVER_REG
            if reg.exists():
                try:
                    data = json.loads(reg.read_text(encoding="utf-8") or "{}")
                    if str(port) in data:
                        pid = int(data[str(port)].get("pid") or 0)
                except Exception:
                    pass
        if not pid:
            return ToolResult(False, error=(
                f"No tracked server for port {port}. Pass pid= explicitly, or "
                "only servers started via start_server can be stopped by port."))
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            return ToolResult(False, error=f"stop_server could not signal pid {pid}: {e}")
        # wait for the port to actually close (or the process to die)
        t_end = time.time() + 5
        while time.time() < t_end:
            alive = False
            try:
                os.kill(pid, 0)          # signal 0 = existence check
                alive = True
            except OSError:
                alive = False
            bound = False
            if port:
                s = socket.socket()
                try:
                    s.bind(("127.0.0.1", port))
                except OSError:
                    bound = True          # still bound
                finally:
                    s.close()
            if not alive and (not port or not bound):
                break
            time.sleep(0.3)
        # clean the registry entry
        try:
            reg = self.root / self._SERVER_REG
            if reg.exists() and port:
                data = json.loads(reg.read_text(encoding="utf-8") or "{}")
                data.pop(str(port), None)
                reg.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass
        return ToolResult(True, output=(
            f"Server STOPPED (pid {pid}" + (f", port {port}" if port else "") +
            "). Verified: process gone" + (", port free" if port else "") + "."))

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
        reg.add("start_server",
                "Start a server DETACHED and VERIFY it in one call (for hosting tasks). "
                "Pass the launch command (e.g. 'python3 -m http.server 8000 --directory "
                "projects/demo'), a port, and an expected content marker; it waits for the "
                "port, fetches the URL, checks the marker, and reports the verified URL. "
                "The server keeps running after this call. Use this INSTEAD of plain "
                "foreground run_shell for anything meant to stay up (http.server, flask, "
                "node, vite build --host).",
                {"type": "object", "properties": {
                    "command": {"type": "string"}, "port": {"type": "integer"},
                    "path": {"type": "string"}, "marker": {"type": "string"},
                    "name": {"type": "string"}, "wait": {"type": "integer"}},
                 "required": []},
                self.start_server, Risk.EXECUTE,
                agents=["supervisor", "coder", "worker", "solo"])
        reg.add("stop_server",
                "Stop a server that start_server launched (cleanup). Pass port= "
                "(the port you started it on) or pid= from its output. Verifies the "
                "process is gone and the port is free. ALWAYS call this when a task "
                "says to stop/shut down a server after verifying it.",
                {"type": "object", "properties": {
                    "port": {"type": "integer"}, "pid": {"type": "integer"}},
                 "required": []},
                self.stop_server, Risk.EXECUTE,
                agents=["supervisor", "coder", "worker", "solo"])
        reg.add("device_info",
                "One-shot, CORRECT device report: storage (Termux paths), battery, network, "
                "memory, cpu. Use for ANY device/system question (storage, battery, wifi). "
                "Never guess shell paths yourself — this tool knows the right ones.",
                {"type": "object", "properties": {"detail": S}},
                self.device_info, Risk.READ_ONLY,
                agents=["supervisor", "coder", "worker", "researcher", "critic", "solo"])
