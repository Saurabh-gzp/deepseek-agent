---
name: Make Automation Script
description: Turn a repetitive manual workflow into a robust, scheduled, self-logging script — CLI args, config, retries, notifications, cron/Termux scheduling and idempotency. Use for "automate my X", "run this daily", backup/sync/report/bot scripts.
tags: [automation, scripting, cron, scheduler, bash, python, termux, workflow]
version: 1.0
agents: ["coder", "worker"]
---

# Skill: Make an Automation Script

## The 6 questions (answer before writing a line)
1. **Trigger** — manual, schedule, file change, webhook?
2. **Input** — where does data come from? What if it is missing?
3. **Action** — the actual work (keep it one function).
4. **Output** — file, API, notification, DB?
5. **Failure** — what happens on error: retry, skip, alert, abort?
6. **Idempotency** — safe to run twice? (Almost always must be yes.)

## Skeleton (copy this every time)
```python
#!/usr/bin/env python3
"""<what it does> — usage: ./script.py --help"""
from __future__ import annotations
import argparse, json, logging, os, sys, time
from datetime import datetime
from pathlib import Path

APP = "myautomation"
HOME = Path(os.getenv("AUTOMATION_HOME", Path.home() / f".{APP}"))
HOME.mkdir(parents=True, exist_ok=True)
STATE = HOME / "state.json"

log = logging.getLogger(APP)

def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(HOME / f"{APP}.log")])

def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}

def save_state(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")          # atomic write
    tmp.write_text(json.dumps(st, indent=2)); tmp.replace(STATE)

def retry(fn, tries: int = 3, base: float = 2.0):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            log.warning("attempt %d/%d failed: %s", i + 1, tries, e)
            time.sleep(base ** i)

def do_work(cfg: dict, state: dict, dry_run: bool) -> dict:
    # ---- the actual automation ----
    return {"processed": 0}

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="show what would happen")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--config", default=str(HOME / "config.json"))
    args = ap.parse_args()

    setup_logging(args.verbose)
    lock = HOME / f"{APP}.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        log.error("another run in progress (%s) — exiting", lock); return 2
    lock.touch()
    try:
        cfg = json.loads(Path(args.config).read_text()) if Path(args.config).exists() else {}
        state = load_state()
        t0 = time.time()
        result = do_work(cfg, state, args.dry_run)
        state["last_run"] = datetime.now().isoformat()
        state["last_result"] = result
        if not args.dry_run:
            save_state(state)
        log.info("done in %.1fs: %s", time.time() - t0, result)
        return 0
    except Exception:
        log.exception("FAILED")
        return 1
    finally:
        lock.unlink(missing_ok=True)

if __name__ == "__main__":
    sys.exit(main())
```

## Non-negotiable features
| Feature | Why |
|---|---|
| `--dry-run` | test destructive logic safely |
| Lock file | no overlapping cron runs |
| State file | resume, dedupe, "since last run" |
| Log file + stdout | debuggable at 3 AM |
| Exit codes (0/1/2) | cron and monitors depend on them |
| Config outside code | change behaviour without editing the script |
| Secrets via env | never commit credentials |
| Atomic writes | crash never corrupts state |

## Scheduling

### Linux cron
```bash
crontab -e
0 */6 * * * /usr/bin/python3 /home/u/scripts/job.py >> /home/u/logs/job.log 2>&1
```

### Termux (Android) — termux-job-scheduler / cronie
```bash
pkg install termux-services cronie termux-api
sv-enable crond
crontab -e     # same syntax
# battery-friendly alternative (survives Doze):
termux-job-scheduler --script ~/scripts/job.sh --period-ms 21600000 --persisted true
# keep the device awake during a long run:
termux-wake-lock; python job.py; termux-wake-unlock
```

### systemd timer (VPS)
```ini
# ~/.config/systemd/user/job.timer
[Unit] Description=job
[Timer] OnCalendar=*-*-* 06:00:00
        Persistent=true
[Install] WantedBy=timers.target
```

## Notifications
```python
# Termux native
os.system(f'termux-notification --title "{APP}" --content "{msg}"')
# Telegram (works everywhere)
requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
              json={"chat_id": CHAT, "text": msg}, timeout=15)
```
Notify on **failure always**, on success only for meaningful events (digest, not spam).

## Idempotency patterns
- Hash the input; skip if the hash is already in state.
- `INSERT ... ON CONFLICT DO NOTHING` for DBs.
- Write to `file.tmp` then `rename` (atomic on POSIX).
- Use "processed IDs" set in state rather than "last index".

## Testing checklist before shipping
```
□ Runs with --dry-run and prints intended actions
□ Runs twice in a row → second run is a no-op or correctly incremental
□ Kill it mid-run → state not corrupted, next run recovers
□ Network off → fails gracefully with a clear log line, exit 1
□ Missing config → helpful message, not a traceback
□ Log file rotates or is bounded (logrotate / maxBytes)
```

## Deliverables
`job.py` · `config.example.json` · `README.md` (install, env vars, cron line, troubleshooting)
· one real successful run pasted as proof.

## Anti-patterns
❌ Hardcoded paths (`/home/john/...`) · ❌ `print()` instead of logging ·
❌ Silent `except: pass` · ❌ No lock file on a cron job ·
❌ Storing secrets in the script · ❌ Unbounded log growth ·
❌ Non-idempotent writes that duplicate data every run
