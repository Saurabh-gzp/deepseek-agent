---
name: Python OOP
description: Object-oriented Python — classes, dataclasses, repositories, SOLID-lite. Use when building an application, inventory system, OOP assignment, class design, service layer, or CRUD backend in Python.
tags: [oop, class, dataclass, repository, solid, python]
version: 1.0
agents: ["coder"]
---

# Skill: Python OOP

## When to use
User wants an *application* (not a one-file script) with domain objects.

## Procedure
1. One module per concern: `models.py` (dataclasses), `repo.py` (SQLite access), `app.py` (CLI or HTTP).
2. Dataclasses for records; no god-class.
3. Repository methods: `add`, `get`, `list`, `report` — SQL stays in the repo.
4. Type hints on public methods.
5. `if __name__ == "__main__"` demo that prints a real query result.
6. App entrypoint must use `from models import X` / `from repo import Y` — **never** `from .models` (that only works as a package and dies as `python3 app.py`).

## Checklist
- [ ] ≥2 classes
- [ ] no business logic inside SQL strings mixed with print()
- [ ] runnable `python3 app.py` produces real output

## Anti-patterns
❌ One 400-line `main.py` with globals
❌ Classes that only wrap a dict with no behaviour
---
