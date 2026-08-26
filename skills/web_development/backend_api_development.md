---
name: Backend API Development
description: Design and build REST/JSON APIs — routing, validation, auth, error contracts, persistence, pagination and deployment. Use when building a server, API endpoints, backend service, FastAPI/Flask/Express app or database layer.
tags: [backend, api, rest, fastapi, flask, express, database, auth, sql]
version: 1.0
agents: ["coder", "supervisor", "worker"]
---

# Skill: Backend API Development

## Design the contract first
Before code, write the endpoint table:
| Method | Path | Body | Returns | Auth |
|---|---|---|---|---|
| GET | /api/items?limit=&cursor= | – | `{items:[],next:null}` | – |
| POST | /api/items | `{name,qty}` | 201 `{id,...}` | Bearer |
| GET | /api/items/{id} | – | `{...}` / 404 | – |
| PATCH | /api/items/{id} | partial | `{...}` | Bearer |
| DELETE | /api/items/{id} | – | 204 | Bearer |

Rules: plural nouns, no verbs in paths, versioned prefix `/api/v1`, plural consistency.

## FastAPI reference implementation
```python
from fastapi import FastAPI, HTTPException, Depends, Query, status
from pydantic import BaseModel, Field
from typing import Optional

app = FastAPI(title="Items API", version="1.0.0")

class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    qty: int = Field(ge=0, default=0)

class ItemOut(ItemIn):
    id: int

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/api/v1/items", response_model=dict)
def list_items(limit: int = Query(20, le=100), cursor: Optional[int] = None,
               db=Depends(get_db)):
    rows = db.query(limit + 1, cursor)
    return {"items": rows[:limit],
            "next": rows[limit].id if len(rows) > limit else None}

@app.post("/api/v1/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, db=Depends(get_db), user=Depends(current_user)):
    return db.insert(body.model_dump())

@app.get("/api/v1/items/{item_id}", response_model=ItemOut)
def get_item(item_id: int, db=Depends(get_db)):
    row = db.get(item_id)
    if not row:
        raise HTTPException(404, detail="Item not found")
    return row
```
Run: `uvicorn app:app --host 0.0.0.0 --port 8000 --reload` → docs at `/docs`.

## Error contract (one shape, everywhere)
```json
{ "error": { "code": "validation_error",
             "message": "qty must be >= 0",
             "field": "qty",
             "request_id": "a1b2c3" } }
```
| Status | Use |
|---|---|
| 400 | malformed request |
| 401 | not authenticated |
| 403 | authenticated but not allowed |
| 404 | not found (also for hidden resources) |
| 409 | conflict / duplicate |
| 422 | semantically invalid |
| 429 | rate limited (+ `Retry-After`) |
| 500 | your bug — log it with the request_id, never leak the traceback |

## Validation
Validate at the boundary with a schema (pydantic/zod/joi). Never trust client input.
Whitelist fields; reject unknown ones. Coerce types explicitly. Bound every list/limit.

## Auth
```python
# Bearer JWT
from jose import jwt
def current_user(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    try:
        payload = jwt.decode(authorization[7:], SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(401, "Invalid token")
    return payload["sub"]
```
- Passwords: bcrypt/argon2, never MD5/SHA alone. Never log tokens.
- Secrets from env. Short-lived access token + refresh token.
- CORS: explicit origin list, never `*` with credentials.

## Persistence
```python
# Parameterised queries ONLY — never f-strings in SQL
cur.execute("SELECT * FROM items WHERE owner = ? AND qty > ?", (user, n))
```
- Index every column you filter/sort on.
- Migrations from day one (alembic / raw numbered .sql files).
- Connection pooling; close connections in `finally`.
- SQLite is fine for small/Termux deployments: `PRAGMA journal_mode=WAL`.

## Cross-cutting must-haves
```
□ /health endpoint (uptime checks)
□ Structured request logging with a request_id
□ Rate limiting on write and auth endpoints
□ Pagination on every list endpoint (cursor > offset at scale)
□ Timeouts on every outbound call
□ Graceful shutdown (drain in-flight requests)
□ .env.example documenting every variable
```

## Testing
```python
from fastapi.testclient import TestClient
client = TestClient(app)

def test_create_and_get():
    r = client.post("/api/v1/items", json={"name": "x", "qty": 2},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 201
    iid = r.json()["id"]
    assert client.get(f"/api/v1/items/{iid}").json()["name"] == "x"

def test_validation_rejects_negative_qty():
    assert client.post("/api/v1/items", json={"name": "x", "qty": -1}).status_code == 422
```
Prove it works with a real `curl` against the running server before declaring done.

## Termux deployment note
`uvicorn app:app --host 0.0.0.0 --port 8080` works on Termux; reach it from the same
Wi-Fi at `http://<phone-ip>:8080`. Use `termux-wake-lock` to keep it alive.
For public access: cloudflared/ngrok tunnel. Do not run production DBs on the phone.

## Anti-patterns
❌ Business logic in route handlers · ❌ returning DB models directly ·
❌ string-interpolated SQL · ❌ 200 OK with `{"error": ...}` inside ·
❌ unbounded list endpoints · ❌ secrets in code · ❌ no timeout on outbound HTTP
