---
name: Web Automation
description: Automate websites — scraping, form filling, login flows, data extraction, monitoring. Covers requests/BeautifulSoup, Playwright/Selenium, API-first discovery, anti-bot handling and Termux constraints. Use for any "scrape X", "auto-login", "monitor a page" or browser-automation task.
tags: [automation, scraping, playwright, selenium, requests, browser, crawler]
version: 1.0
agents: ["coder", "researcher", "worker"]
---

# Skill: Web Automation

## Decision tree — pick the cheapest tool that works

```
Is there a public/JSON API or an XHR endpoint the page calls?
  YES -> use requests/httpx directly.        ← 95% faster, no browser
  NO  -> Is the content in the initial HTML? (curl the URL and grep)
          YES -> requests + BeautifulSoup / regex
          NO  -> JS-rendered: Playwright (headless)
                 Termux/no-browser? -> find the XHR endpoint in DevTools Network tab, or
                                       use r.jina.ai / a rendering proxy
```

**Always look for the API first.** Open DevTools → Network → XHR, reload, and copy the
request. On the CLI: `curl -s URL | grep -o 'https://[^"]*api[^"]*' | sort -u`.

## Testing local web apps — lifecycle + reconnaissance
*(pattern adapted from Anthropic's webapp-testing Agent Skill, Apache-2.0)*

**Server lifecycle:** never block the agent on a foreground server. Start it in
the background, wait for the port, run checks, then kill it:

```bash
cd app && nohup python -m http.server 8080 >/dev/null 2>&1 &
for i in $(seq 1 20); do curl -s localhost:8080 >/dev/null && break; sleep 0.5; done
curl -s localhost:8080 | head -40          # smoke test
kill %1 2>/dev/null
```

**Reconnaissance-then-action** (for dynamic pages, browser or no browser):
1. Fetch the page and WAIT for everything to settle (Playwright: `networkidle`)
2. Screenshot / dump the DOM first — do not guess selectors
3. Identify selectors from the RENDERED state, not the source template
4. Only then execute the action with the discovered selectors

**Static vs dynamic decision:**
- Static HTML? Read the file directly — selectors are right there.
- Dynamic? Find the XHR/fetch endpoint the page calls and hit THAT with
  requests (95% faster than a browser). Browser only as a last resort.


```python
import requests
from bs4 import BeautifulSoup

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

r = s.get(url, timeout=20)
r.raise_for_status()
soup = BeautifulSoup(r.text, "html.parser")   # lxml if installed

for card in soup.select("div.product-card"):
    yield {
        "title": card.select_one("h3").get_text(strip=True),
        "price": card.select_one(".price").get_text(strip=True),
        "url":   requests.compat.urljoin(url, card.select_one("a")["href"]),
    }
```

### Selector rules
- Prefer `data-*` attributes and IDs → then stable classes → never nth-child chains.
- Always guard: `el = soup.select_one(sel); if not el: continue` — never assume.
- Extract with `get_text(strip=True)`, normalise whitespace, cast numbers explicitly.

## Tier 2 — Playwright (JS-rendered)
```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 800},
                              locale="en-US")
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)

    page.wait_for_selector("div.product-card", timeout=15000)   # never sleep()
    # intercept the real API instead of scraping DOM when possible:
    # page.on("response", lambda r: print(r.url) if "api" in r.url else None)

    items = page.eval_on_selector_all("div.product-card", """els => els.map(e => ({
        title: e.querySelector('h3')?.innerText?.trim(),
        price: e.querySelector('.price')?.innerText?.trim()
    }))""")
    ctx.close(); browser.close()
```
**Termux note:** Playwright/Chromium usually does NOT run on Termux (no Android Chromium build).
Options: (1) find the JSON API, (2) run the browser step on a PC/VPS, (3) use a rendering
service. State this limitation instead of silently failing.

## Login flows
```python
# 1. Try the API login endpoint first
r = s.post("https://site.com/api/login", json={"email": E, "password": P}, timeout=20)
token = r.json()["token"]
s.headers["Authorization"] = f"Bearer {token}"

# 2. HTML form login: fetch the page, carry CSRF + cookies
page = s.get(login_url); soup = BeautifulSoup(page.text, "html.parser")
csrf = soup.select_one('input[name="csrf_token"]')["value"]
s.post(login_url, data={"csrf_token": csrf, "user": E, "pass": P})
# verify: check for a logged-in-only element, don't trust the status code
```
Credentials come from **env vars**, never hardcoded. Never store cookies in the repo.

## Pagination patterns
```python
# offset
for page in range(1, 100):
    data = s.get(api, params={"page": page, "limit": 50}).json()
    if not data["items"]: break
    yield from data["items"]

# cursor
cursor = None
while True:
    data = s.get(api, params={"cursor": cursor}).json()
    yield from data["items"]
    cursor = data.get("next_cursor")
    if not cursor: break

# infinite scroll (Playwright)
prev = 0
while True:
    page.mouse.wheel(0, 20000); page.wait_for_timeout(1200)
    n = page.locator(".item").count()
    if n == prev: break
    prev = n
```

## Reliability harness (use for every scraper)
```python
import time, random, json, pathlib

def fetch(url, tries=3):
    for i in range(tries):
        try:
            r = s.get(url, timeout=20)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 30))); continue
            r.raise_for_status()
            return r
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(2 ** i + random.random())

def save_checkpoint(rows, path="out.jsonl"):
    with open(path, "a") as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False) + "\n")
```
- Randomised delay 1–3 s between requests; never hammer.
- Checkpoint to JSONL after every page so a crash loses nothing.
- Log the URL with every error so failures are reproducible.

## Etiquette & legality (do this, always)
- Read `/robots.txt` and honour `Disallow` for the paths you touch.
- Respect `Retry-After` and rate limits; identify yourself in the UA when appropriate.
- Public data only. No paywalled/PII scraping, no login-wall bypass, no CAPTCHA solving services.
- If a site's ToS forbids automation, say so and stop — report it to the user.

## Anti-bot reality check
| Signal | Meaning | Response |
|---|---|---|
| 403 instantly | UA/header fingerprint | full browser headers, session |
| Cloudflare interstitial | JS challenge | Playwright, or stop |
| Content missing in HTML | client-rendered | find XHR API |
| 429 | rate limit | back off, respect Retry-After |
| Rotating class names | build-hashed CSS | anchor on text/structure, not classes |

## Output contract
Ship: `scraper.py` + `requirements.txt` + `README.md` (usage, env vars, sample output)
+ `out.jsonl`/`out.csv`. Run it and paste the first 3 real rows as proof before declaring done.

## Anti-patterns
❌ `time.sleep(5)` instead of `wait_for_selector` · ❌ scraping the DOM when an API exists ·
❌ no error handling on `select_one` · ❌ hardcoded credentials ·
❌ unbounded loops without a page cap · ❌ claiming success without showing extracted rows
