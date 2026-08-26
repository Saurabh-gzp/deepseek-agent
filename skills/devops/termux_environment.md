---
name: Termux Environment
description: Work correctly inside Termux on Android — package management, storage, permissions, background jobs, networking, memory limits and common build failures. Use whenever running commands, installing packages, scheduling jobs or debugging environment errors on a phone.
tags: [termux, android, mobile, environment, packages, cron, storage]
version: 1.0
agents: ["coder", "worker", "supervisor", "critic"]
---

# Skill: Termux Environment

## Know where you are
```bash
echo $PREFIX          # /data/data/com.termux/files/usr  → you are in Termux
uname -m              # aarch64 typically
nproc; free -h        # cores and RAM (often 4–8 GB, shared with Android)
df -h $HOME
```
Termux is **not** a normal Linux: no `/usr/bin`, no systemd, no root, Android may kill
background processes, and many binary wheels have no ARM build.

## Packages
```bash
pkg update && pkg upgrade -y
pkg install -y python git curl wget openssh nano
pkg install -y clang make cmake pkg-config libffi openssl   # build toolchain
pkg install -y libxml2 libxslt libjpeg-turbo freetype       # common C deps
pkg install -y python-numpy python-pandas                   # PREFER prebuilt over pip
```
**Rule:** if `pip install X` tries to compile, check `pkg search python-X` first.
Known pip pain on ARM: `numpy`, `pandas`, `scipy`, `lxml`, `pillow`, `cryptography`,
`matplotlib` → install via `pkg`. `playwright`/`chromium` → not available, do not try.

```bash
pip install --upgrade pip wheel
pip install rich pyyaml requests beautifulsoup4     # pure python, always fine
```

## Storage & permissions
```bash
termux-setup-storage                 # grant once; creates ~/storage/
~/storage/shared                     # /sdcard — visible to other apps
~/storage/downloads
```
Files inside `$HOME` are private to Termux. To hand a result to the user's gallery/file
manager, copy it to `~/storage/shared/`. `chmod +x` works in `$HOME` but not on `/sdcard`.

## Keep jobs alive
```bash
termux-wake-lock          # before a long run — prevents Doze from killing it
python long_job.py
termux-wake-unlock
```
Also: Android Settings → Battery → Termux → **Unrestricted**, and enable the Termux
notification. Without this, background jobs die after a few minutes of screen-off.

## Scheduling
```bash
pkg install cronie termux-services
sv-enable crond
crontab -e
  0 */6 * * * $PREFIX/bin/python $HOME/scripts/job.py >> $HOME/logs/job.log 2>&1

# battery-aware alternative (survives reboot, Android-native):
pkg install termux-api
termux-job-scheduler --script $HOME/scripts/job.sh --period-ms 21600000 --persisted true
```

## Networking
- Ports below 1024 are blocked; use 8080/8000.
- Bind `0.0.0.0` to reach the server from another device on the same Wi-Fi.
- Find the phone's IP: `ifconfig 2>/dev/null | grep inet` or `termux-wifi-connectioninfo`.
- No `ping` by default (`pkg install iputils`); use `curl -I` for reachability.

## Memory: the #1 cause of mysterious failures
```
Killed          ← OOM killer, not your code crashing
```
Mitigations: stream/chunk large files, `chunksize=` in pandas, avoid loading whole
datasets, close file handles, prefer SQLite over in-memory dicts for big data,
run one heavy process at a time.

## Useful termux-api commands
```bash
termux-notification --title "Done" --content "Job finished"
termux-clipboard-set "text"; termux-clipboard-get
termux-battery-status          # JSON — pause heavy work under 20%
termux-tts-speak "task complete"
termux-share -a send file.pdf
termux-toast "quick message"
```

## Troubleshooting table
| Symptom | Cause | Fix |
|---|---|---|
| `Killed` | OOM | chunk the work, reduce batch size |
| wheel build error | no ARM wheel | `pkg install python-<pkg>` or pure-python alt |
| `Permission denied` on /sdcard | Android FS | work in `$HOME`, copy out at the end |
| process dies on screen-off | Doze | `termux-wake-lock` + unrestricted battery |
| `command not found` | `$PREFIX/bin` not in PATH | `export PATH=$PREFIX/bin:$PATH` |
| pip SSL errors | old certs | `pkg upgrade openssl ca-certificates` |
| git push asks password | no keys | `ssh-keygen -t ed25519`, add to GitHub |
| slow everything | thermal throttling | shorter bursts, let it cool |

## Agent behaviour rules on Termux
1. Check `$PREFIX` before assuming a Linux layout.
2. Never suggest `sudo`, `apt-get`, or systemd — they don't exist here.
3. Prefer stdlib solutions; every compiled dependency is a possible dead end.
4. Long commands: add explicit timeouts and stream output, don't buffer 100 MB.
5. Tell the user when something genuinely can't run on a phone (Docker, Chromium,
   CUDA, heavy training) instead of failing three times first.
