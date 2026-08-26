---
name: Termux Environment
description: Work correctly inside Termux on Android — package management, storage, permissions, background jobs, networking, memory limits and common build failures. Use whenever running commands, installing packages, scheduling jobs or debugging environment errors on a phone.
tags: [termux, android, mobile, environment, packages, cron, storage]
version: 2.0
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

## Device & hardware — termux-api (the RIGHT way)
Requires BOTH: the `Termux:API` Android app (F-Droid) AND `pkg install termux-api`.
All commands output JSON — parse with python, e.g.
`termux-battery-status | python3 -c "import sys,json; print(json.load(sys.stdin)['percentage'])"`.

| Need | Command |
|---|---|
| Battery (%, temp, health, plugged) | `termux-battery-status` |
| Storage info | `termux-storage-get` (file picker) / `df -h` for sizes |
| Wi-Fi state | `termux-wifi-connectioninfo` · scan list: `termux-wifi-scaninfo` |
| GPS location | `termux-location` (add `-p gps` or `-p network` for provider) |
| Sensors (live) | `termux-sensor -l` then `termux-sensor -s <name> -d <ms> -n <count>` |
| Clipboard | `termux-clipboard-get` / `termux-clipboard-set "text"` |
| Notification | `termux-notification --title T --content C` |
| Torch / vibrate | `termux-torch on` / `termux-vibrate -d 500` |
| Screen brightness | `termux-brightness 150` (0–255) |
| Volume | `termux-volume music 10` |
| Camera | `termux-camera-photo -c 0 photo.jpg` (info: `termux-camera-info`) |
| Mic recording | `termux-microphone-record -f rec.m4a -l 30` |
| TTS speak | `termux-tts-speak "done"` |
| Call/SMS | `termux-telephony-call` / `termux-sms-send -n NUMBER MSG` (log: `termux-call-log`, `termux-sms-list`) |
| Download/share/open | `termux-download URL` / `termux-share file` / `termux-open file_or_url` |
| Device info | `termux-telephony-deviceinfo`, `termux-info`, `termux-torch`, `getprop ro.product.model` |

If a `termux-*` command prints nothing or errors: the Termux:API app is missing —
say so and fall back to `/sys` and `df`.

## Storage — what actually works on Android
- **Sizes:** `df -h` — look at `/storage/emulated` and `/data` rows.
  `df | grep storage` filters it fast. THIS is the source for "how much storage".
- **Reading df -h correctly (CRITICAL):** rows like `/dev/block/dm-*`, `/system`,
  `/system_ext`, `/product`, `/vendor` are READ-ONLY ANDROID SYSTEM partitions —
  they are ALWAYS ~100% full and that is NORMAL. They are NOT the user's storage.
  Report user storage from `/data` (apps+app data) and `/storage/emulated`
  (shared storage) rows only. NEVER sum the dm-* rows and claim
  "your phone storage is full" — that is a false alarm and a plain wrong answer.
- **Folder sizes:** `du -sh ~/storage/shared/* 2>/dev/null` — needs storage
  permission (below); scanning all of /sdcard is SLOW — target subfolders,
  add a timeout, and expect `Permission denied` on some dirs (Android 11+
  scoped storage). ONE failed `du` is enough — do not retry it blindly.
- **Shared files:** must run `termux-setup-storage` ONCE (user taps Allow);
  it creates `~/storage/{shared,downloads,pictures,dcim,music,movies,external}`.
  `~/storage/shared` = `/storage/emulated/0` (= `/sdcard`).
- **SD card:** writable only at
  `/storage/XXXX-XXXX/Android/data/com.termux/files/` (Android rule).

## Commands that DO NOT exist in Termux — never retry them
`lsblk`, `blockdev`, `fdisk` (blocked), `systemctl`, `service`, `sudo`, `su`
(no root), `chmod` on /sdcard (ignored by fuse), `/dev/block/*` access,
`dmesg` (blocked). If `command not found` (exit 127): the package isn't
installed — try `pkg install <tool>` ONCE, and if apt can't provide it,
STOP and report honestly. `sudo` doesn't exist — there is no root.

## Package management
```bash
pkg update && pkg upgrade -y
pkg install <name>        # e.g. python, git, nodejs, openssh, termux-api, jq, nano
pkg search <term>         # before "package not found" conclusions
pip install --upgrade pip wheel   # before pip installs (avoid legacy-setup crashes)
```
Heavy builds (numpy/pandas on old devices): prefer `pkg install python-numpy`
over `pip install numpy`.

## Networking
`ping -c 4 host` · `curl`/`wget` · `ip addr` (not ifconfig by default) ·
`ss -tuln` (netstat may need net-tools) · `termux-wifi-connectioninfo`.
Android forbids privileged ports (<1024) and raw sockets for some tools.

## Background & keep-alive
- `nohup cmd >/dev/null 2>&1 &` for background; `termux-wake-lock` stops
  Android from freezing the session during long work (`termux-wake-unlock` after).
- Long-running servers ALWAYS in background, then `curl localhost:PORT` to verify.
- Scheduled jobs: `termux-job-scheduler` or `crond` from `pkg install cronie`.

## Reality checklist before reporting device facts
1. Did the command ACTUALLY run and return data? Paste the REAL output.
2. Numbers in the report must come from tool output THIS run — never estimates,
   "example outputs" or remembered specs (e.g. "64GB" without a df row proving it).
3. If everything failed, say exactly what failed and why — an honest
   "couldn't read X because Y" beats a made-up table.
