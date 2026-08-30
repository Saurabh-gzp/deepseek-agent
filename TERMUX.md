# Running DeepSeek-Agent on Termux (3 minutes)

Turn an Android phone into a personal AI-agent workstation.

## Step 1 — Install Termux

Get Termux from [F-Droid](https://f-droid.org/en/packages/com.termux/)
(the Play Store build is outdated). Open it and run:

```bash
pkg update && pkg upgrade -y
pkg install -y python git
```

## Step 2 — Get the agent & run setup

```bash
git clone https://github.com/Saurabh-gzp/deepseek-agent.git && cd deepseek-agent
bash setup.sh
```

setup.sh installs Python dependencies automatically and prints the launch
command. If anything fails, a minimal `python` + `rich` + `PyYAML` install
is still enough to run.

## Step 3 — Log in with your DeepSeek account

Launch the agent:

```bash
deepseek
```

The first run opens the **DeepSeek login wizard** by itself — enter your
**DeepSeek** account email + password (the same account you use on
chat.deepseek.com). The bearer token is stored only on your device
(`keys/`, chmod 600, gitignored) and auto-refreshes when it expires.
If no browser is available, it falls back to a **paste-token** mode so the
WAF-protected login still works on Termux.

`setup.sh` also installs a global `deepseek` command (removing any older
`deepseek` command or alias first), so you can launch from anywhere with:

```bash
deepseek
```

That's it. Now just talk to it:

```
deepseek ❯ hello, who are you?
deepseek ❯ make a python script that prints squares from 1 to 10, run it and show me the output
deepseek ❯ clean the workspace, delete everything
```

## Termux extras

```bash
pkg install -y termux-api        # then: battery, wifi, sensors via termux-api
pkg install -y python-numpy      # faster RAG search than pip numpy
```

- Playwright/Chromium do not run on Termux — the web-automation skill covers
  the workarounds.
- `--update` mode: after `git pull`, run `bash setup.sh --update` to refresh
  dependencies while keeping your keys.
