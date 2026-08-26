---
name: SQLite DBMS
description: Design and query SQLite databases — schema, INSERT, JOIN, reports. Use when the user asks for a database, DBMS, SQL, sqlite, inventory, CRUD store, shop.db, or to persist structured rows. Do NOT use for JSON config files.
tags: [sqlite, sql, dbms, database, schema, join]
version: 1.1
agents: ["coder", "worker"]
---

# Skill: SQLite DBMS

## When to use
Need a real `.db` file with tables, not a CSV dump.

## Procedure
1. Put the file at `projects/<slug>/<name>.db`.
2. `sqlite_exec(db_path=..., sql='CREATE TABLE ...')` — one statement per call.
3. Insert sample rows (3+).
4. `sqlite_schema(db_path=...)` to prove the schema.
5. Run a `SELECT ... JOIN` and paste the real rows in the task output.
6. Application code must use `sqlite3` stdlib (or the same db path) — never a second in-memory fake.

## Schema rules
- INTEGER PRIMARY KEY, NOT NULL on required columns
- Foreign keys declared; `PRAGMA foreign_keys=ON` in app code
- No `SELECT *` in app reports — name columns

## Checklist
- [ ] `.db` file exists in the project folder
- [ ] schema tool output shown
- [ ] at least one JOIN query with real numbers
- [ ] app (if any) points at the same file

## Anti-patterns
❌ Hardcoding rows only in HTML with no database
❌ Claiming "DB ready" without `sqlite_schema` output
❌ Writing SQL inside `run_python` when `sqlite_exec` exists
---
