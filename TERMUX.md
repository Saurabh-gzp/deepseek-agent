# Running Nexus Agent on Termux (3 minutes)

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
git clone https://github.com/Saurabh-gzp/nexus-agent.git && cd nexus-agent
bash setup.sh
```

setup.sh installs Python dependencies automatically and prints the launch
command. If anything fails, a minimal `python` + `rich` + `PyYAML` install
is still enough to run.

## Step 3 — Add a key

Launch the agent:

```bash
python3 nexus.py
```

The first run opens the key wizard by itself — paste your **Mistral AI**
key (free: [console.mistral.ai](https://console.mistral.ai)). Keys are
stored only on your device (`keys/`, chmod 600, gitignored). Add more
anytime with `/keys add sk-...`.

That's it. Now just talk to it:

```
nexus ❯ hello, who are you?
nexus ❯ make a python script that prints squares from 1 to 10, run it and show me the output
nexus ❯ clean the workspace, delete everything
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
