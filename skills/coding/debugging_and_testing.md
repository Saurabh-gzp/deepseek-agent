---
name: Debugging and Testing
description: Systematically find and fix bugs, and write tests that actually catch regressions. Use whenever code fails, tests break, output is wrong, or before declaring any coding task complete.
tags: [debugging, testing, pytest, errors, troubleshooting, verification]
version: 1.0
agents: ["coder", "critic", "worker"]
---

# Skill: Debugging & Testing

## The loop
```
REPRODUCE → ISOLATE → HYPOTHESISE → TEST ONE THING → FIX → VERIFY → REGRESSION-TEST
```
Never skip REPRODUCE. If you cannot make it fail on demand, you cannot know you fixed it.

## 1. Reproduce
- Run the exact failing command and capture the FULL traceback (not a summary).
- Note: inputs, environment, Python/Node version, working directory.
- Make it minimal: strip everything until the bug is a 10-line script.

## 2. Read the error properly
```
Traceback (most recent call last):
  File "app.py", line 42, in load        ← YOUR code, closest to bottom = start here
    data = json.loads(raw)
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
                              ↑ the actual fact: `raw` was empty or not JSON
```
Read bottom-up. The last line is *what*; your deepest frame is *where*.
Common translations:
| Error | Real cause |
|---|---|
| `NoneType has no attribute X` | a lookup returned None — check the source of that value |
| `KeyError: 'x'` | assumed schema; print `list(d)` |
| `ModuleNotFoundError` | wrong venv / not installed / src layout not installed |
| `ConnectionError` | network, DNS, or wrong URL — curl it |
| `IndexError` | empty list from a filter/query that matched nothing |
| exit 137 | OOM killed (common on Termux) |

## 3. Isolate with evidence, not vibes
```python
print(f"DEBUG raw={raw!r} type={type(raw)} len={len(raw)}")   # !r shows quotes/whitespace
import traceback; traceback.print_stack()                      # how did we get here
breakpoint()                                                    # pdb: n, s, c, p var, w, q
```
Bisect: comment out half the pipeline. Which half still fails? Repeat.
For "it worked yesterday": `git diff`, `git bisect`, check dependency versions.

## 4. Fix rules
- Fix the **cause**, not the symptom. `if x is None: return` hides the real bug.
- One change at a time; re-run after each.
- If a test fails, fix the implementation — only change the test when the spec truly changed.
- Keep the diff minimal; unrelated refactors hide regressions.

## 5. Verify — the part agents skip
A task is NOT done until you have executed it and seen correct output.
```bash
python app.py                 # exit 0?
pytest -q                     # all green?
echo $?                       # actually check
curl -s localhost:8000/health # for servers
cat output.json | head -5     # for data jobs: look at the real rows
```
Then read the produced file back to confirm it exists and has the right content.

## Writing tests that matter
```python
def test_parses_valid_row():                 # happy path
    assert parse("a,1") == {"name": "a", "n": 1}

@pytest.mark.parametrize("bad", ["", "a", "a,b", None])
def test_rejects_bad_rows(bad):              # edges + errors
    with pytest.raises(ValueError):
        parse(bad)

def test_handles_unicode():                  # the bug you just fixed
    assert parse("café,2")["name"] == "café"
```
Priority: (1) the bug you just fixed, (2) boundaries (0, 1, empty, huge, unicode, None),
(3) error paths, (4) the happy path. Coverage % is a hint, not a goal.

## Fixture and isolation hygiene
- `tmp_path` for files — never write into the repo during tests.
- No network in unit tests; stub the client at the boundary.
- Tests must pass in any order (no shared mutable state).
- Fast: unit suite under 5 seconds or people stop running it.

## Termux-specific gotchas
| Symptom | Fix |
|---|---|
| `Killed` mid-run | OOM — process data in chunks, lower batch size |
| wheel build fails | `pkg install clang make libffi openssl` or find a pure-Python alternative |
| `Permission denied` on /sdcard | run `termux-setup-storage` |
| Long job dies when screen locks | `termux-wake-lock` |
| Port bind fails <1024 | use ports ≥ 1024 |

## Escalation ladder (when stuck > 15 min)
1. Re-read the error message literally — the answer is usually in it.
2. Print the actual types and values at the boundary.
3. Search the exact error string on the web.
4. Read the library source (`python -c "import x; print(x.__file__)"`).
5. Write the smallest failing repro and reason from first principles.
6. Try a different approach entirely — sunk cost is not evidence.

## Anti-patterns
❌ "It should work now" without running it · ❌ `except Exception: pass` ·
❌ changing 5 things at once · ❌ deleting the failing test ·
❌ adding `sleep()` to fix a race · ❌ trusting the model's memory of an API over the docs
