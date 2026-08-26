# Nexus Agent — Termux pe chalane ka tarika (3 minute)

## Step 0 — Zip ko Termux me daalo

1. Ye zip apne phone ke `Download` folder me rakho
2. Termux khologe pehli baar to storage permission do:
   ```
   termux-setup-storage
   ```
3. Zip ko Termux home me copy karo:
   ```
   cp /sdcard/Download/nexus-agent-v1.1.zip ~
   ```

## Step 1 — Unzip

```
pkg install unzip -y
cd ~
unzip nexus-agent-v1.1.zip
cd nexus-agent
```

## Step 2 — Install (ek baar karna hai)

```
bash install.sh
```

Ye khud karega: python + dependencies install, folders banana, offline tests chalana.
Agar kuch fail ho to bhi chalega — `python` + `rich` + `PyYAML` ho to kaam chal jata hai.

## Step 3 — API key

Zip me `.env` already hai ( tumhari Mistral keys ke saath — **is zip ko kisi ko share mat karna, warna keys leak ho jayengi**).

Nayi key add karni ho to (best tarika — app ke andar se):
```
nexus ❯ /key     # menu khulega → 1. Mistral → a (add) → key paste → t 1 (test)
```
Keys `keys/mistral.json` me save hoti hain (delete bhi /key se).
Ya seedha .env me: `nano .env` (MISTRAL_API_KEY=... likh ke save)

## Step 4 — Chalao!

```
python nexus.py
```

Bas. Ab seedha baat karo:
```
nexus ❯ namaste, tum kaun ho?
nexus ❯ /help
nexus ❯ /keys
nexus ❯ ek python script banao jo 1 se 10 squares print kare, run karke output dikhao
```

## Roz ka istemal

```
cd ~/nexus-agent
python nexus.py
```
(install.sh ne `nexus` command bhi banaya ho to kahin se bhi `nexus` likh ke chala sakte ho)

## Phone ke liye zaroori settings

- **Battery optimization hatana**: Android Settings → Apps → Termux → Battery → *Unrestricted*
  (warna screen off hote hi agent ka kaam ruk jayega)
- Lambe tasks ke liye: `termux-wake-lock` chala do pehle
- Har chalane par data `~/nexus-agent/.nexus/` me save hota hai (memory, RAG, sessions)

## Common problems

| Problem | Fix |
|---|---|
| `python: not found` | `pkg install python` |
| TUI me color/corrupt dikhe | `pkg install termux-tools` + Termux settings me *Terminal margin* kam karo |
| 429 / rate-limit messages | Normal hai — 2 keys pe kaam chalta rahega, thoda slow hoga |
| Storage permission error | `termux-setup-storage` dobara chalao |
| Kuch aur toot jaye | `python3 -m pytest tests/test_core.py -q` — 101 tests pass hone chahiye |
