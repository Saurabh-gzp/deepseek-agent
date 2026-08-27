---
name: TempMail Ninja
description: Disposable email + OTP receiver CLI at /home/user/temp-mail/tempmail.py (adjust path to the cloned repo). Use for ANY task needing a throwaway email address, signup automation, or receiving OTP/verification codes from the terminal. Zero dependencies, pure stdlib.
tags: [automation, email, otp, signup, tempmail, verification]
version: 1.0
agents: ["coder", "worker", "researcher"]
---

# Skill: TempMail Ninja (disposable email + OTP)

## THE CLI CONTRACT (memorize — never invent another service)

```bash
python3 <repo>/tempmail.py --new                 # stdout: ONLY the fresh address
python3 <repo>/tempmail.py --new myname          # custom username
python3 <repo>/tempmail.py --status              # address + time-left + inbox count
python3 <repo>/tempmail.py --inbox               # list messages
python3 <repo>/tempmail.py --inbox --json        # machine-readable
python3 <repo>/tempmail.py --otp --timeout 120   # BLOCKS until next OTP → ONLY the code
python3 <repo>/tempmail.py --watch               # live event stream
python3 <repo>/tempmail.py --delete              # destroy account
```

- Session persists at `~/.tempmail_ninja_session.json` — separate CLI calls share
  the SAME address until it expires (~10 min). `--new` without a username keeps
  the current session? NO — `--new` creates a NEW address; plain `--status`
  reuses the existing one.
- Exit codes: `0` ok · `1` timeout/error · `2` bad usage.
- Backend: mail.tm public API with auto-failover to mail.gw. Addresses look like
  `wordword1234@emalupe.com`.

## NON-NEGOTIABLE RULES

1. **ALWAYS use this CLI** — never 1secmail/guerrillamail/mail.tm-direct-API
   scripts unless the task explicitly says so. Live failure: an agent burned
   600s hitting 1secmail (403) while the working CLI sat in the task description.
2. **OTP capture**: start the site action FIRST (submit email), THEN run
   `--otp --timeout 120` — it polls the inbox and prints the code when it lands.
3. **Timeouts**: a fresh address is alive ~10 minutes. If `--otp` exits 1, the
   mail may have not arrived — check `--inbox --json` before giving up.
4. Report the address used + the OTP received as real tool output. Never fake it.
