---
name: Python Project Structure
description: Lay out a production-grade Python project — src layout, packaging, config, logging, testing, typing and CI. Use when starting a new Python project, a CLI tool, or refactoring a script pile into a real package.
tags: [python, structure, packaging, pytest, architecture, cli]
version: 1.0
agents: ["coder", "supervisor", "worker"]
---

# Skill: Python Project Structure

## Standard layout
```
project/
├── pyproject.toml          # single source of truth (PEP 621)
├── README.md
├── .gitignore
├── .env.example
├── src/mypkg/              # src layout = no accidental local imports
│   ├── __init__.py         # __version__ only
│   ├── __main__.py         # python -m mypkg
│   ├── cli.py              # argparse/typer entry
│   ├── config.py           # settings loading
│   ├── core/               # domain logic (no I/O)
│   ├── services/           # I/O: http, db, filesystem
│   ├── models/             # dataclasses / pydantic schemas
│   └── utils/
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/
└── scripts/
```

**Rule:** `core/` never imports `services/`. Dependencies point inward. That is what makes
the code testable without mocks everywhere.

## pyproject.toml
```toml
[project]
name = "mypkg"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["httpx>=0.27", "rich>=13"]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "ruff", "mypy"]

[project.scripts]
mypkg = "mypkg.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
```

## Module conventions
```python
"""One-line module purpose."""
from __future__ import annotations   # cheap forward refs, 3.9 compatible

import os                # stdlib
from pathlib import Path

import httpx             # third-party

from .config import Settings   # local
```
- `pathlib.Path`, never string paths.
- Type hints on every public function.
- Dataclasses for data; no dicts-as-objects across module boundaries.
- Custom exception base per package: `class MyPkgError(Exception)`.
- No logic at import time; everything behind functions.

## Config pattern
```python
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    api_key: str
    timeout: int = 30
    debug: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        key = os.getenv("API_KEY")
        if not key:
            raise RuntimeError("API_KEY is required (see .env.example)")
        return cls(api_key=key,
                   timeout=int(os.getenv("TIMEOUT", "30")),
                   debug=os.getenv("DEBUG", "").lower() in ("1", "true"))
```

## Testing
```python
# tests/conftest.py
import pytest
@pytest.fixture
def tmp_project(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path

# tests/unit/test_core.py
import pytest
from mypkg.core.parser import parse

@pytest.mark.parametrize("raw,expected", [("1,2", [1, 2]), ("", [])])
def test_parse(raw, expected):
    assert parse(raw) == expected

def test_parse_invalid():
    with pytest.raises(ValueError, match="malformed"):
        parse("!!")
```
Test the behaviour, not the implementation. One assert-concept per test.
`pytest -q` must exit 0 before any task is called done.

## CLI entry
```python
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
        return 0
    except MyPkgError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
```
Return exit codes, don't `sys.exit()` deep inside logic.

## Termux/mobile notes
- Prefer stdlib + pure-Python deps; compiled wheels (numpy, lxml, pandas) may need
  `pkg install python-numpy` or build tools.
- `pkg install python clang libxml2 libxslt` before pip-installing compiled packages.
- Keep the dependency count low — every extra wheel is a possible build failure on ARM.

## Definition of done
```
□ `pip install -e .` works from a clean venv
□ `python -m mypkg --help` prints usage
□ `pytest -q` passes
□ `ruff check .` clean
□ README has install + one runnable example
□ No secrets in the repo; .env.example present
```
