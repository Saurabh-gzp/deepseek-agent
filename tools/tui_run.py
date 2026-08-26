#!/usr/bin/env python3
"""Bounded run driver: launch the nexus TUI in a real pty, send ONE task,
capture the full ANSI-stripped transcript. For 24/7 use: set NEXUS_OUT per
run; task text via NEXUS_TASK. Aborts on true silence (stall > 600s) or when
the app returns to the prompt — never hangs forever."""
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("NEXUS_OUT", "/home/user/tui_run_log.txt")
TASK = os.environ.get("NEXUS_TASK", "make a best portfolio website for yourself search on internet claude ai skills about frontend design and use it and host kr dena locally best ui ke sath bnana")
COLS, ROWS = 120, 50
PROMPT_MARK = "nexus"

os.makedirs("/home/user/tui_artifacts", exist_ok=True)


def main():
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    env = dict(os.environ)
    env.pop("NEXUS_FANCY_INPUT", None)
    env.setdefault("TERM", "xterm-256color")
    proc = subprocess.Popen([sys.executable, "nexus.py", "-t", "cyber", "-m", "smart"],
                            cwd=ROOT, stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    raw = bytearray()

    def pump(wait=1.0):
        end = time.time() + wait
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.3)
            if r:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return
                if not data:
                    return
                raw.extend(data)

    def text():
        return raw.decode("utf-8", "replace")

    # banner
    pump(4.0)

    def at_prompt():
        t = text()
        lines = [l for l in t.splitlines() if l.strip()]
        return bool(lines) and "❯" in lines[-1][-6:]

    # wait for first prompt
    for _ in range(40):
        if at_prompt():
            break
        pump(0.5)

    os.write(master, (TASK + "\n").encode())
    print(f"TASK SENT at {time.strftime('%H:%M:%S')}", flush=True)

    # wait until prompt returns again (task done) or 1500s
    deadline = time.time() + 1500
    last_len = -1
    stall = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            print("PROCESS EXITED", flush=True)
            break
        if at_prompt() and len(text()) > 400:
            # confirm the prompt is really back (2 consecutive reads)
            pump(1.5)
            if at_prompt():
                print("PROMPT BACK — task finished", flush=True)
                break
        pump(1.0)
        n = len(raw)
        if n == last_len:
            stall += 1
            if stall > 600:            # 10 min true silence (long blocking calls redraw in place)
                print("STALL — forcing done", flush=True)
                break
        else:
            stall = 0
            last_len = n

    pump(2.0)
    try:
        os.write(master, b"/exit\n")
    except Exception:
        pass
    time.sleep(1.5)
    try:
        proc.terminate()
    except Exception:
        pass

    t = text()
    clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", t)
    clean = re.sub(r"\x1b\][^\x07]*\x07", "", clean)
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(clean)
    print(f"TRANSCRIPT SAVED: {len(clean)} chars -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
