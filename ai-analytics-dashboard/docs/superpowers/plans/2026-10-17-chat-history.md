# Per-user Chat History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each user's conversations in Postgres (`app.chats`, `app.chat_turns`) and let them list, reopen and delete chats from the Ask page.

**Architecture:** Two new SQLAlchemy models. A small `app/chats.py` service holds all DB logic (create chat, ownership lookup, build LLM context, save a turn). `api/chats.py` exposes list/get/delete. `api/query.py` creates/loads the chat, builds context server-side, and saves every turn (success or failure) in its existing `finally`. The React Ask page gets a `ChatSidebar` and sends `chat_id` instead of client-built history.

**Tech Stack:** FastAPI, SQLAlchemy 2 async + asyncpg, pytest + pytest-asyncio + httpx (backend); React 18, react-query, Vitest + Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-10-17-chat-history-design.md`

## Global Constraints

- `app.users`, registration, login and bcrypt hashing are **unchanged**. `query_audit` is **unchanged** and still written on every request.
- New tables live in schema `app`; created by the existing `Base.metadata.create_all` in `init_db`. No Alembic.
- Ownership: every chat query filters on `Chat.user_id == user.id`. Missing **or** foreign chat id → HTTP 404, code `not_found`, message `Chat not found.` (no existence leak).
- Persistence must never break a query: failures in `save_turn` are logged and swallowed (same rule as `_audit`).
- Title = question whitespace-collapsed, truncated to 120 chars.
- Context sent to the planner = last 3 turns of the chat whose `response.answerable` is true, as `{"question": ..., "answer": response["answer"][:4000]}`, oldest first. When `chat_id` is set, the request body's `history` is ignored.
- Answer cache key is used only when the resulting history is empty (unchanged rule).
- Error responses for `/api/query` keep their existing `{"error": {"code", "message"}}` shape and gain `"chat_id"` inside `error` when a chat exists. (Addition to the spec: the client needs the id to continue a conversation whose first turn failed.)
- Admin endpoints never return chat contents.
- Commits: stage **explicit paths only** (the working tree has unrelated uncommitted Gemini/script changes). End every commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

### Running backend integration tests (no image build)

The integration tests need Postgres (with the `analytics_test` DB from `db/init`), Redis and Mailpit. From the project root in cmd:

```cmd
docker compose up -d --no-build postgres redis mailpit
run-tests.bat tests\integration\test_chats_service.py -q
```

`run-tests.bat` is created in Task 0. Unit-style tests that only need the code run the same way.

## Review Focus

1. A user passes another user's `chat_id` to `POST /api/query` → 404, and **no** turn or chat is written for the owner (Task 3 test).
2. Two turns saved concurrently into one chat → unique `(chat_id, turn_index)` makes one insert fail; `save_turn` returns `None` and does not raise (Task 1 test).
3. Very long, whitespace-padded or non-ASCII first question → title ≤ 120 chars, never empty (Task 1 test).
4. Deleting a chat that is not yours or does not exist → 404 and the real chat survives (Task 2 test).
5. Cached answer path (`cache_hit=true`) still records a turn (Task 3 test).
6. `chat_id` of the wrong type (`"abc"`) → 422 `validation_error`, not a 500 (Task 3 test).
7. Switching or deleting chats while a question is in flight must not corrupt the visible conversation (Task 5: sidebar disabled while busy; test).

---

## File Structure

| File | Responsibility |
|---|---|
| `run-tests.bat` (create) | Sets local test env vars and runs pytest in `backend\.venv` |
| `backend/app/db/models.py` (modify) | `Chat`, `ChatTurn` models |
| `backend/app/chats.py` (create) | DB logic: `create_chat`, `get_owned_chat`, `load_context`, `save_turn`, `make_title` |
| `backend/app/api/chats.py` (create) | `GET /api/chats`, `GET /api/chats/{id}`, `DELETE /api/chats/{id}` |
| `backend/app/api/errors.py` (modify) | `api_error(..., **extra)` |
| `backend/app/api/query.py` (modify) | `chat_id` in/out, server-side context, `save_turn` in `finally` |
| `backend/app/main.py` (modify) | register `chats.router` |
| `backend/tests/integration/conftest.py` (modify) | truncate new tables |
| `backend/tests/integration/test_chats_service.py` (create) | Task 1 tests |
| `backend/tests/integration/test_chats_api.py` (create) | Task 2 + 3 tests |
| `frontend/src/api/client.ts`, `api/types.ts` (modify) | `ApiError.chatId`, chat types |
| `frontend/src/components/ChatSidebar.tsx` (+ test) (create) | chat list UI |
| `frontend/src/pages/Ask.tsx` (modify) (+ `Ask.test.tsx` create) | use `chat_id`, load/resume chats |
| `README.md` (modify) | document chat history |

---

### Task 0: Local test runner

**Files:**
- Create: `run-tests.bat`

- [ ] **Step 1: Create `run-tests.bat`**

```bat
@echo off
rem Runs backend tests locally against the Docker Postgres/Redis/Mailpit (no image build).
rem Usage: run-tests.bat tests\integration\test_chats_service.py -q
setlocal
cd /d "%~dp0backend"

if not exist .venv\Scripts\python.exe (
  python -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -c "import pytest, asgi_lifespan" 2>nul || .venv\Scripts\python.exe -m pip install -r requirements-dev.txt || exit /b 1

rem Values from .env.example defaults; override by setting them before calling this script.
if not defined POSTGRES_USER set "POSTGRES_USER=analytics"
if not defined POSTGRES_PASSWORD set "POSTGRES_PASSWORD=analyticsdevpw"
if not defined POSTGRES_DB set "POSTGRES_DB=analytics"
if not defined QUERY_RO_PASSWORD set "QUERY_RO_PASSWORD=queryrodevpw"

set "DATABASE_URL=postgresql+asyncpg://%POSTGRES_USER%:%POSTGRES_PASSWORD%@127.0.0.1:5432/%POSTGRES_DB%_test"
set "QUERY_RO_URL=postgresql://query_ro:%QUERY_RO_PASSWORD%@127.0.0.1:5432/%POSTGRES_DB%_test"
set "REDIS_URL=redis://127.0.0.1:6379/0"
set "MAILPIT_API=http://127.0.0.1:8025"
set "SMTP_HOST=127.0.0.1"
set "ADMIN_PASSWORD=Test@123"
set "ADMIN_EMAIL=admin@analytics.local"
set "UPLOAD_DIR=%TEMP%\test_uploads"

.venv\Scripts\python.exe -m pytest %*
```

- [ ] **Step 2: Verify the existing suite starts**

Run: `docker compose up -d --no-build postgres redis mailpit` then `run-tests.bat tests\integration\test_auth.py -q`
Expected: tests run and pass (confirms the env block is right). If the DB `analytics_test` is missing, the Postgres volume predates `db/init/01-init.sh`; run `docker compose exec postgres createdb -U analytics analytics_test` and then the extension setup from that script, or recreate the volume.

- [ ] **Step 3: Commit**

```bash
git add run-tests.bat
git commit -m "chore: local backend test runner for the no-build workflow"
```

---

### Task 1: Models and chat service

**Files:**
- Modify: `backend/app/db/models.py` (add after `QueryAudit`)
- Modify: `backend/tests/integration/conftest.py:17-21` (truncate list)
- Create: `backend/app/chats.py`
- Test: `backend/tests/integration/test_chats_service.py`

**Interfaces:**
- Produces (used by Tasks 2 and 3):
  - `make_title(question: str) -> str`
  - `async create_chat(sessionmaker, user_id: int, question: str) -> int`
  - `async get_owned_chat(session: AsyncSession, chat_id: int, user_id: int) -> Chat | None`
  - `async load_context(sessionmaker, chat_id: int) -> list[dict]` (`[{"question": str, "answer": str}]`, oldest first, max 3)
  - `async save_turn(sessionmaker, chat_id: int, *, question: str, resp: dict | None, status: str, error: str | None, latency_ms: int, cache_hit: bool) -> int | None` (turn id, or `None` if saving failed)
  - Models `Chat(id, user_id, title, created_at, updated_at)`, `ChatTurn(id, chat_id, turn_index, question, response, status, error, latency_ms, cache_hit, created_at)`

- [ ] **Step 1: Write the failing tests** — `backend/tests/integration/test_chats_service.py`

```python
import asyncio

import pytest
from sqlalchemy import select

from app.chats import create_chat, get_owned_chat, load_context, make_title, save_turn
from app.db.models import Chat, ChatTurn, User


def canned(answer="Total is 42.", answerable=True):
    return {"answer": answer, "answerable": answerable, "unverified_numbers": [], "charts": [], "tables": [],
            "sources": [], "sql": [], "plan": None, "steps": []}


async def make_user(db, email="u@example.com") -> int:
    async with db.sessionmaker() as s:
        u = User(email=email, password_hash="x", role="user", is_verified=True)
        s.add(u)
        await s.commit()
        return u.id


def test_make_title_collapses_whitespace_and_truncates():
    assert make_title("  What   is\n the  total? ") == "What is the total?"
    assert len(make_title("x" * 500)) == 120
    assert make_title("é" * 200) == "é" * 120
    assert make_title("   ") == "New chat"


async def test_create_chat_sets_owner_and_title(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "  revenue   by region ")
    async with db.sessionmaker() as s:
        chat = await get_owned_chat(s, chat_id, uid)
        assert chat.title == "revenue by region" and chat.user_id == uid
        assert await get_owned_chat(s, chat_id, uid + 999) is None
        assert await get_owned_chat(s, chat_id + 999, uid) is None


async def test_save_turn_assigns_increasing_indexes_and_bumps_updated_at(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q1")
    async with db.sessionmaker() as s:
        before = (await s.get(Chat, chat_id)).updated_at
    t1 = await save_turn(db.sessionmaker, chat_id, question="q1", resp=canned(), status="ok", error=None, latency_ms=5, cache_hit=False)
    t2 = await save_turn(db.sessionmaker, chat_id, question="q2", resp=None, status="timeout", error="exceeded 150s", latency_ms=9, cache_hit=False)
    assert t1 and t2 and t1 != t2
    async with db.sessionmaker() as s:
        turns = (await s.scalars(select(ChatTurn).where(ChatTurn.chat_id == chat_id).order_by(ChatTurn.turn_index))).all()
        assert [t.turn_index for t in turns] == [0, 1]
        assert turns[1].response is None and turns[1].status == "timeout" and turns[1].error == "exceeded 150s"
        assert (await s.get(Chat, chat_id)).updated_at > before


async def test_load_context_returns_last_three_answerable_oldest_first(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q0")
    for i in range(5):
        await save_turn(db.sessionmaker, chat_id, question=f"q{i}", resp=canned(f"a{i}"), status="ok", error=None, latency_ms=1, cache_hit=False)
    await save_turn(db.sessionmaker, chat_id, question="unanswerable", resp=canned("nope", answerable=False), status="unanswerable", error=None, latency_ms=1, cache_hit=False)
    await save_turn(db.sessionmaker, chat_id, question="failed", resp=None, status="timeout", error="x", latency_ms=1, cache_hit=False)
    ctx = await load_context(db.sessionmaker, chat_id)
    assert ctx == [{"question": f"q{i}", "answer": f"a{i}"} for i in (2, 3, 4)]


async def test_load_context_truncates_long_answers(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q")
    await save_turn(db.sessionmaker, chat_id, question="q", resp=canned("a" * 9000), status="ok", error=None, latency_ms=1, cache_hit=False)
    assert len((await load_context(db.sessionmaker, chat_id))[0]["answer"]) == 4000


async def test_concurrent_saves_never_raise_and_indexes_stay_unique(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q")
    results = await asyncio.gather(*[
        save_turn(db.sessionmaker, chat_id, question=f"q{i}", resp=canned(), status="ok", error=None, latency_ms=1, cache_hit=False)
        for i in range(5)
    ])
    assert any(r is not None for r in results)
    async with db.sessionmaker() as s:
        idx = (await s.scalars(select(ChatTurn.turn_index).where(ChatTurn.chat_id == chat_id))).all()
        assert len(idx) == len(set(idx))
```

- [ ] **Step 2: Run to verify it fails**

Run: `run-tests.bat tests\integration\test_chats_service.py -q`
Expected: collection error `ImportError: cannot import name 'create_chat' from 'app.chats'` (module doesn't exist).

- [ ] **Step 3: Add models** — append to `backend/app/db/models.py` after the `QueryAudit` class, and add `UniqueConstraint` to the `sqlalchemy` import on line 4:

```python
class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    __table_args__ = (UniqueConstraint("chat_id", "turn_index"), {"schema": "app"})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("app.chats.id", ondelete="CASCADE"), index=True)
    turn_index: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Import line becomes:
`from sqlalchemy import BigInteger, Boolean, Computed, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func`

- [ ] **Step 4: Update test DB reset** — in `backend/tests/integration/conftest.py` change the TRUNCATE string to
`"TRUNCATE app.chat_turns, app.chats, app.query_audit, app.dashboard_pins, app.dataset_columns, app.email_codes, "`
(keep the rest of the statement as is).

- [ ] **Step 5: Implement `backend/app/chats.py`**

```python
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat, ChatTurn

log = logging.getLogger(__name__)

TITLE_MAX = 120
CONTEXT_TURNS = 3
CONTEXT_ANSWER_MAX = 4000


def make_title(question: str) -> str:
    return " ".join(question.split())[:TITLE_MAX] or "New chat"


async def create_chat(sessionmaker, user_id: int, question: str) -> int:
    async with sessionmaker() as s:
        chat = Chat(user_id=user_id, title=make_title(question))
        s.add(chat)
        await s.commit()
        return chat.id


async def get_owned_chat(session: AsyncSession, chat_id: int, user_id: int) -> Chat | None:
    return await session.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))


async def load_context(sessionmaker, chat_id: int) -> list[dict]:
    """Last answerable turns as planner history, oldest first."""
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(ChatTurn.question, ChatTurn.response)
                .where(ChatTurn.chat_id == chat_id, ChatTurn.response.is_not(None))
                .order_by(ChatTurn.turn_index.desc())
                .limit(20)
            )
        ).all()
    answerable = [(q, r) for q, r in rows if r.get("answerable")]
    latest = answerable[:CONTEXT_TURNS]
    return [{"question": q, "answer": str(r.get("answer", ""))[:CONTEXT_ANSWER_MAX]} for q, r in reversed(latest)]


async def save_turn(
    sessionmaker,
    chat_id: int,
    *,
    question: str,
    resp: dict | None,
    status: str,
    error: str | None,
    latency_ms: int,
    cache_hit: bool,
) -> int | None:
    """Persist one turn. Never raises: returns the turn id, or None if saving failed."""
    try:
        async with sessionmaker() as s:
            next_index = await s.scalar(select(func.coalesce(func.max(ChatTurn.turn_index), -1) + 1).where(ChatTurn.chat_id == chat_id))
            turn = ChatTurn(
                chat_id=chat_id,
                turn_index=next_index,
                question=question[:2000],
                response=resp,
                status=status,
                error=error,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
            )
            s.add(turn)
            chat = await s.get(Chat, chat_id)
            if chat is not None:
                chat.updated_at = func.now()
            await s.commit()
            return turn.id
    except Exception:  # noqa: BLE001 - persistence must never break a query
        log.exception("Failed to save chat turn")
        return None
```

- [ ] **Step 6: Run to verify it passes**

Run: `run-tests.bat tests\integration\test_chats_service.py -q`
Expected: all pass. If `test_save_turn_assigns_increasing_indexes_and_bumps_updated_at` fails on the `updated_at >` comparison because both timestamps fall in one transaction-time tick, it is a real bug in `save_turn` (it must use `func.now()` in its own committed transaction, as written); do not loosen the assertion.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/models.py backend/app/chats.py backend/tests/integration/conftest.py backend/tests/integration/test_chats_service.py
git commit -m "feat(chats): chat and chat_turn models with service layer"
```

---

### Task 2: Chats API (list, get, delete)

**Files:**
- Create: `backend/app/api/chats.py`
- Modify: `backend/app/main.py:14` (import) and `backend/app/main.py:22` (`ROUTERS`)
- Test: `backend/tests/integration/test_chats_api.py`

**Interfaces:**
- Consumes: `Chat`, `ChatTurn`, `current_user`, `get_session`, `api_error`, `create_chat`, `save_turn` (Task 1).
- Produces (consumed by the frontend, Task 5):
  - `GET /api/chats?limit&offset` → `{"items": [{"id", "title", "updated_at", "turn_count"}], "total"}`
  - `GET /api/chats/{id}` → `{"id", "title", "created_at", "turns": [{"id", "turn_index", "question", "response", "status", "error", "created_at"}]}`
  - `DELETE /api/chats/{id}` → 204

- [ ] **Step 1: Write the failing tests** — create `backend/tests/integration/test_chats_api.py`

```python
from sqlalchemy import select

from app.chats import create_chat, save_turn
from app.db.models import Chat, ChatTurn, User
from helpers import auth, register_verified_user


def canned(answer="Total is 42.", answerable=True):
    return {"answer": answer, "answerable": answerable, "unverified_numbers": [], "charts": [], "tables": [],
            "sources": [], "sql": [], "plan": None, "steps": []}


async def admin_id(client) -> int:
    async with client.app.state.sessionmaker() as s:
        return (await s.scalar(select(User).where(User.role == "admin"))).id


async def seed_chat(client, user_id: int, question: str, turns: int = 1) -> int:
    sm = client.app.state.sessionmaker
    chat_id = await create_chat(sm, user_id, question)
    for i in range(turns):
        await save_turn(sm, chat_id, question=f"{question} #{i}", resp=canned(f"a{i}"), status="ok", error=None, latency_ms=3, cache_hit=False)
    return chat_id


async def test_requires_auth(client):
    assert (await client.get("/api/chats")).status_code == 401
    assert (await client.get("/api/chats/1")).status_code == 401
    assert (await client.delete("/api/chats/1")).status_code == 401


async def test_list_returns_only_own_chats_newest_first_with_turn_count(client, admin_token):
    admin = await admin_id(client)
    first = await seed_chat(client, admin, "first", turns=2)
    second = await seed_chat(client, admin, "second", turns=1)
    _, other_token = await register_verified_user(client)
    async with client.app.state.sessionmaker() as s:
        other = (await s.scalar(select(User).where(User.role == "user"))).id
    await seed_chat(client, other, "not yours")

    body = (await client.get("/api/chats", headers=auth(admin_token))).json()
    assert body["total"] == 2
    assert [c["id"] for c in body["items"]] == [second, first]
    assert [c["turn_count"] for c in body["items"]] == [1, 2]
    assert {"id", "title", "updated_at", "turn_count"} <= set(body["items"][0])

    theirs = (await client.get("/api/chats", headers=auth(other_token))).json()
    assert [c["title"] for c in theirs["items"]] == ["not yours"]


async def test_get_returns_turns_in_order(client, admin_token):
    chat_id = await seed_chat(client, await admin_id(client), "topic", turns=3)
    body = (await client.get(f"/api/chats/{chat_id}", headers=auth(admin_token))).json()
    assert body["title"] == "topic"
    assert [t["turn_index"] for t in body["turns"]] == [0, 1, 2]
    assert body["turns"][0]["question"] == "topic #0"
    assert body["turns"][0]["response"]["answer"] == "a0"
    assert body["turns"][0]["status"] == "ok"


async def test_get_and_delete_of_foreign_or_missing_chat_is_404(client, admin_token):
    chat_id = await seed_chat(client, await admin_id(client), "private")
    _, other_token = await register_verified_user(client)
    for r in (
        await client.get(f"/api/chats/{chat_id}", headers=auth(other_token)),
        await client.delete(f"/api/chats/{chat_id}", headers=auth(other_token)),
        await client.get("/api/chats/999999", headers=auth(admin_token)),
        await client.delete("/api/chats/999999", headers=auth(admin_token)),
    ):
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    # the real chat survived the foreign delete attempt
    assert (await client.get(f"/api/chats/{chat_id}", headers=auth(admin_token))).status_code == 200


async def test_delete_cascades_turns(client, admin_token):
    chat_id = await seed_chat(client, await admin_id(client), "gone", turns=2)
    r = await client.delete(f"/api/chats/{chat_id}", headers=auth(admin_token))
    assert r.status_code == 204
    async with client.app.state.sessionmaker() as s:
        assert await s.get(Chat, chat_id) is None
        assert (await s.scalars(select(ChatTurn).where(ChatTurn.chat_id == chat_id))).all() == []


async def test_list_pagination(client, admin_token):
    admin = await admin_id(client)
    for i in range(3):
        await seed_chat(client, admin, f"c{i}")
    page = (await client.get("/api/chats?limit=2&offset=1", headers=auth(admin_token))).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert (await client.get("/api/chats?limit=0", headers=auth(admin_token))).status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `run-tests.bat tests\integration\test_chats_api.py -q`
Expected: FAIL — `404` for `/api/chats` (router not registered) in `test_list_returns_only_own_chats...`, and `test_requires_auth` gets 404 not 401.

- [ ] **Step 3: Implement `backend/app/api/chats.py`**

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_session
from app.api.errors import api_error
from app.chats import get_owned_chat
from app.db.models import Chat, ChatTurn, User

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _not_found():
    return api_error(404, "not_found", "Chat not found.")


@router.get("")
async def list_chats(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    turn_count = select(func.count(ChatTurn.id)).where(ChatTurn.chat_id == Chat.id).correlate(Chat).scalar_subquery()
    rows = (
        await session.execute(
            select(Chat, turn_count)
            .where(Chat.user_id == user.id)
            .order_by(Chat.updated_at.desc(), Chat.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(Chat).where(Chat.user_id == user.id))
    items = [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat(), "turn_count": n} for c, n in rows]
    return {"items": items, "total": total or 0}


@router.get("/{chat_id}")
async def get_chat(chat_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    chat = await get_owned_chat(session, chat_id, user.id)
    if chat is None:
        raise _not_found()
    turns = (await session.scalars(select(ChatTurn).where(ChatTurn.chat_id == chat.id).order_by(ChatTurn.turn_index))).all()
    return {
        "id": chat.id,
        "title": chat.title,
        "created_at": chat.created_at.isoformat(),
        "turns": [
            {
                "id": t.id,
                "turn_index": t.turn_index,
                "question": t.question,
                "response": t.response,
                "status": t.status,
                "error": t.error,
                "created_at": t.created_at.isoformat(),
            }
            for t in turns
        ],
    }


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(chat_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    chat = await get_owned_chat(session, chat_id, user.id)
    if chat is None:
        raise _not_found()
    await session.delete(chat)
    await session.commit()
```

- [ ] **Step 4: Register the router** — in `backend/app/main.py` change the import to
`from app.api import admin, auth, chats, dashboard, datasets, query` and `ROUTERS` to
`ROUTERS = [auth.router, datasets.router, query.router, chats.router, dashboard.router, admin.router]`.

- [ ] **Step 5: Run to verify it passes**

Run: `run-tests.bat tests\integration\test_chats_api.py tests\integration\test_chats_service.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/chats.py backend/app/main.py backend/tests/integration/test_chats_api.py
git commit -m "feat(chats): list, open and delete chats API"
```

---

### Task 3: Wire chats into `POST /api/query`

**Files:**
- Modify: `backend/app/api/errors.py:11-12` (`api_error`)
- Modify: `backend/app/api/query.py` (`QueryIn`, `query`)
- Test: append to `backend/tests/integration/test_chats_api.py`

**Interfaces:**
- Consumes: `create_chat`, `get_owned_chat`, `load_context`, `save_turn` (Task 1).
- Produces: `/api/query` accepts `chat_id: int | None`; success body gains `chat_id`, `turn_id`; error bodies gain `error.chat_id` when a chat exists.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/integration/test_chats_api.py`

```python
import pytest

from app.llm.provider import LLMError


@pytest.fixture
def fake_answer(monkeypatch):
    """Replace the agent pipeline; record the history the endpoint passes in."""
    seen = []

    async def _answer(state, question, history):
        seen.append({"question": question, "history": history})
        if "boom" in question:
            raise LLMError("simulated outage")
        return canned(f"Answer to: {question}")

    monkeypatch.setattr("app.api.query.answer_question", _answer)
    return seen


async def ask(client, token, question, **extra):
    return await client.post("/api/query", json={"question": question, **extra}, headers=auth(token))


async def test_first_question_creates_chat_with_turn(client, admin_token, fake_answer):
    r = await ask(client, admin_token, "How many orders?")
    assert r.status_code == 200
    body = r.json()
    assert body["chat_id"] and body["turn_id"]
    detail = (await client.get(f"/api/chats/{body['chat_id']}", headers=auth(admin_token))).json()
    assert detail["title"] == "How many orders?"
    assert [t["question"] for t in detail["turns"]] == ["How many orders?"]
    assert detail["turns"][0]["response"]["answer"] == "Answer to: How many orders?"
    assert detail["turns"][0]["status"] == "ok"


async def test_followup_appends_and_uses_server_side_history(client, admin_token, fake_answer):
    first = (await ask(client, admin_token, "first question")).json()
    second = (await ask(client, admin_token, "second question", chat_id=first["chat_id"],
                        history=[{"question": "client lies", "answer": "ignored"}])).json()
    assert second["chat_id"] == first["chat_id"]
    assert fake_answer[1]["history"] == [{"question": "first question", "answer": "Answer to: first question"}]
    detail = (await client.get(f"/api/chats/{first['chat_id']}", headers=auth(admin_token))).json()
    assert [t["turn_index"] for t in detail["turns"]] == [0, 1]


async def test_without_chat_id_legacy_history_still_reaches_planner(client, admin_token, fake_answer):
    await ask(client, admin_token, "legacy", history=[{"question": "hi", "answer": "hello"}])
    assert fake_answer[0]["history"] == [{"question": "hi", "answer": "hello"}]


async def test_foreign_chat_id_is_404_and_writes_nothing(client, admin_token, fake_answer):
    mine = (await ask(client, admin_token, "mine")).json()["chat_id"]
    _, other_token = await register_verified_user(client)
    r = await ask(client, other_token, "sneaky", chat_id=mine)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert len(fake_answer) == 1  # pipeline never ran for the foreign request
    async with client.app.state.sessionmaker() as s:
        assert len((await s.scalars(select(ChatTurn).where(ChatTurn.chat_id == mine))).all()) == 1
        assert len((await s.scalars(select(Chat))).all()) == 1


async def test_failed_question_is_saved_and_error_carries_chat_id(client, admin_token, fake_answer):
    r = await ask(client, admin_token, "boom please")
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "llm_unavailable" and err["chat_id"]
    detail = (await client.get(f"/api/chats/{err['chat_id']}", headers=auth(admin_token))).json()
    turn = detail["turns"][0]
    assert turn["response"] is None and turn["status"] == "llm_unavailable" and "simulated outage" in turn["error"]
    # a later question can continue the same chat
    ok = await ask(client, admin_token, "recover", chat_id=err["chat_id"])
    assert ok.status_code == 200 and ok.json()["chat_id"] == err["chat_id"]


async def test_cache_hit_still_records_a_turn(client, admin_token, fake_answer):
    a = (await ask(client, admin_token, "cache me")).json()
    b = (await ask(client, admin_token, "cache me")).json()
    assert a["cache_hit"] is False and b["cache_hit"] is True
    assert len(fake_answer) == 1
    assert a["chat_id"] != b["chat_id"]  # each fresh question without chat_id starts its own chat
    detail = (await client.get(f"/api/chats/{b['chat_id']}", headers=auth(admin_token))).json()
    assert detail["turns"][0]["response"]["answer"] == "Answer to: cache me"


async def test_non_integer_chat_id_is_validation_error(client, admin_token, fake_answer):
    r = await ask(client, admin_token, "x", chat_id="abc")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_save_failure_does_not_break_the_query(client, admin_token, fake_answer, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.chats.ChatTurn", broken)  # make save_turn's insert blow up
    r = await ask(client, admin_token, "still works")
    assert r.status_code == 200 and r.json()["answer"] == "Answer to: still works"
    assert r.json()["turn_id"] is None


async def test_query_audit_still_written(client, admin_token, fake_answer):
    from app.db.models import QueryAudit

    await ask(client, admin_token, "audited")
    async with client.app.state.sessionmaker() as s:
        assert (await s.scalar(select(QueryAudit.question))) == "audited"
```

- [ ] **Step 2: Run to verify they fail**

Run: `run-tests.bat tests\integration\test_chats_api.py -q`
Expected: the new tests FAIL (`KeyError: 'chat_id'`, `422` for unknown `chat_id` handling, etc.). Task 2 tests still pass.

- [ ] **Step 3: Extend `api_error`** — in `backend/app/api/errors.py` replace the function with:

```python
def api_error(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})
```

(`http_exception_handler` already returns the whole `detail` dict as `error`, so extra keys flow through.)

- [ ] **Step 4: Update `backend/app/api/query.py`**

Change imports: add `from app.chats import create_chat, get_owned_chat, load_context, save_turn`.

Change `QueryIn`:

```python
class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)
    chat_id: int | None = None
```

Replace the body of `query()` from `history = [...]` to the end with:

```python
    chat_id = body.chat_id
    if chat_id is not None:
        async with state.sessionmaker() as s:
            if await get_owned_chat(s, chat_id, user.id) is None:
                raise api_error(404, "not_found", "Chat not found.")
        history = await load_context(state.sessionmaker, chat_id)
    else:
        history = [h.model_dump() for h in body.history[-3:]]
        chat_id = await create_chat(state.sessionmaker, user.id, question)

    def fail(status_code: int, code: str, message: str):
        return api_error(status_code, code, message, chat_id=chat_id)

    async with state.sessionmaker() as s:
        version = await get_catalog_version(s)
    key = answer_key(version, question) if not history else None

    resp, status, error, cache_hit, turn_id = None, "ok", None, False, None
    try:
        if key and (cached := await state.cache.get_json(key)) is not None:
            resp, cache_hit = cached, True
        else:
            # Overall deadline below nginx's 180 s proxy timeout, so clients always get a JSON error
            resp = await asyncio.wait_for(answer_question(state, question, history), timeout=state.settings.query_timeout_s)
            if not resp.get("answerable"):
                status = "unanswerable"
            elif any(s["status"] != "ok" for s in resp["steps"]):
                status = "partial"
            if key and _cacheable(resp):
                await state.cache.set_json(key, resp, state.settings.cache_ttl_s)
    except PlanValidationError as exc:
        status, error = "plan_failed", str(exc)
        raise fail(422, "plan_failed", "I couldn't turn this question into a valid analysis plan. Try rephrasing it.")
    except LLMQuotaError as exc:
        status, error = "llm_quota_exhausted", str(exc)
        raise fail(503, status, "The AI provider's API credits or quota are exhausted. Ask the administrator to check AI provider API billing and project limits.")
    except LLMError as exc:
        status, error = "llm_unavailable", str(exc)
        raise fail(502, "llm_unavailable", "The language model is unavailable right now. Please try again shortly.")
    except asyncio.TimeoutError:
        status, error = "timeout", f"exceeded {state.settings.query_timeout_s}s"
        raise fail(504, "query_timeout", "This question took too long to answer. Try a narrower question or retry shortly.")
    finally:
        latency = int((time.perf_counter() - started) * 1000)
        await _audit(state, user.id, question, resp, status, error, latency, cache_hit)
        turn_id = await save_turn(
            state.sessionmaker, chat_id, question=question, resp=resp, status=status, error=error, latency_ms=latency, cache_hit=cache_hit
        )

    return {**resp, "cache_hit": cache_hit, "request_id": request_id, "chat_id": chat_id, "turn_id": turn_id}
```

Note: cache hit responses must not leak a stale `chat_id`/`turn_id`: the cached object is the `answer_question` result, which has neither key, and they are only added in the final `return`.

- [ ] **Step 5: Run to verify it passes**

Run: `run-tests.bat tests\integration\test_chats_api.py tests\integration\test_chats_service.py tests\integration\test_query_api.py -q`
Expected: all pass, including the **unchanged** `test_query_api.py` (guards against regressions in the existing flow). If `test_save_failure_does_not_break_the_query` errors because `monkeypatch.setattr("app.chats.ChatTurn", broken)` makes `ChatTurn(...)` raise inside the `try`, that is the intended failure path; if instead the test passes without exercising it, `save_turn` is not wrapping the constructor in its `try` — move it inside.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/errors.py backend/app/api/query.py backend/tests/integration/test_chats_api.py
git commit -m "feat(chats): persist every query turn and build context server-side"
```

---

### Task 4: Frontend API types and error plumbing

**Files:**
- Modify: `frontend/src/api/client.ts:1-8` (`ApiError`) and the throw at the end of `api()`
- Modify: `frontend/src/api/types.ts` (add types; extend `QueryResponse`)
- Test: `frontend/src/api/client.test.ts` (create)

**Interfaces:**
- Produces (used by Task 5):
  - `ApiError.chatId?: number`
  - Types `ChatSummary { id: number; title: string; updated_at: string; turn_count: number }`, `ChatList { items: ChatSummary[]; total: number }`, `ChatTurnRecord { id: number; turn_index: number; question: string; response: QueryResponse | null; status: string; error: string | null; created_at: string }`, `ChatDetail { id: number; title: string; created_at: string; turns: ChatTurnRecord[] }`
  - `QueryResponse` gains `chat_id: number; turn_id: number | null`

- [ ] **Step 1: Write the failing test** — `frontend/src/api/client.test.ts`

```ts
import { ApiError, api } from "./client";

describe("api error handling", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("exposes chat_id from an error body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
        json: async () => ({ error: { code: "llm_unavailable", message: "down", chat_id: 7 } }),
      }),
    );
    const err = await api("/query", { body: { question: "q" } }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.chatId).toBe(7);
    expect(err.code).toBe("llm_unavailable");
  });

  it("leaves chatId undefined when the error has none", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "x", json: async () => ({ error: { code: "not_found", message: "no" } }) }),
    );
    const err = await api("/chats/1").catch((e) => e);
    expect(err.chatId).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run (in `frontend`): `npm test -- src/api/client.test.ts`
Expected: FAIL — `expected undefined to be 7`.

- [ ] **Step 3: Implement** — `ApiError` becomes:

```ts
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public chatId?: number,
  ) {
    super(message);
  }
}
```

and the throw in `api()` becomes:

```ts
    throw new ApiError(res.status, data?.error?.code ?? "error", data?.error?.message ?? res.statusText ?? "Request failed", data?.error?.chat_id);
```

In `types.ts` add `chat_id: number; turn_id: number | null;` to `QueryResponse` (after `request_id`) and append:

```ts
export interface ChatSummary {
  id: number;
  title: string;
  updated_at: string;
  turn_count: number;
}

export interface ChatList {
  items: ChatSummary[];
  total: number;
}

export interface ChatTurnRecord {
  id: number;
  turn_index: number;
  question: string;
  response: QueryResponse | null;
  status: string;
  error: string | null;
  created_at: string;
}

export interface ChatDetail {
  id: number;
  title: string;
  created_at: string;
  turns: ChatTurnRecord[];
}
```

- [ ] **Step 4: Run to verify it passes**

Run (in `frontend`): `npm test -- src/api/client.test.ts` then `npm run build`
Expected: tests pass; `tsc --noEmit` succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/types.ts frontend/src/api/client.test.ts
git commit -m "feat(frontend): chat types and chat_id on API errors"
```

---

### Task 5: Ask page sidebar and resumable chats

**Files:**
- Create: `frontend/src/components/ChatSidebar.tsx`, `frontend/src/components/ChatSidebar.test.tsx`
- Modify: `frontend/src/pages/Ask.tsx`
- Create: `frontend/src/pages/Ask.test.tsx`

**Interfaces:**
- Consumes: `ChatSummary`, `ChatList`, `ChatDetail`, `QueryResponse`, `ApiError.chatId` (Task 4); endpoints from Tasks 2–3.
- Produces: `ChatSidebar` props `{ chats: ChatSummary[]; activeId: number | null; busy: boolean; onSelect(id: number): void; onDelete(id: number): void; onNew(): void }`.

- [ ] **Step 1: Write the failing sidebar test** — `frontend/src/components/ChatSidebar.test.tsx`

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import ChatSidebar from "./ChatSidebar";

const chats = [
  { id: 2, title: "Revenue by region", updated_at: "2026-10-17T10:00:00Z", turn_count: 2 },
  { id: 1, title: "Salary by department", updated_at: "2026-10-16T10:00:00Z", turn_count: 1 },
];

function setup(over = {}) {
  const props = { chats, activeId: null, busy: false, onSelect: vi.fn(), onDelete: vi.fn(), onNew: vi.fn(), ...over };
  render(<ChatSidebar {...props} />);
  return props;
}

describe("ChatSidebar", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists chats and selects one", () => {
    const p = setup();
    fireEvent.click(screen.getByText("Salary by department"));
    expect(p.onSelect).toHaveBeenCalledWith(1);
  });

  it("starts a new conversation", () => {
    const p = setup();
    fireEvent.click(screen.getByRole("button", { name: /new conversation/i }));
    expect(p.onNew).toHaveBeenCalled();
  });

  it("deletes only after confirmation", () => {
    const p = setup();
    vi.stubGlobal("confirm", vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true));
    const del = screen.getByRole("button", { name: /delete revenue by region/i });
    fireEvent.click(del);
    expect(p.onDelete).not.toHaveBeenCalled();
    fireEvent.click(del);
    expect(p.onDelete).toHaveBeenCalledWith(2);
  });

  it("disables everything while a question is in flight", () => {
    const p = setup({ busy: true });
    fireEvent.click(screen.getByText("Revenue by region"));
    expect(p.onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /new conversation/i })).toBeDisabled();
  });

  it("shows an empty state", () => {
    setup({ chats: [] });
    expect(screen.getByText(/no saved conversations/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run (in `frontend`): `npm test -- src/components/ChatSidebar.test.tsx`
Expected: FAIL — cannot resolve `./ChatSidebar`.

- [ ] **Step 3: Implement `frontend/src/components/ChatSidebar.tsx`**

```tsx
import type { ChatSummary } from "../api/types";

interface Props {
  chats: ChatSummary[];
  activeId: number | null;
  busy: boolean;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onNew: () => void;
}

export default function ChatSidebar({ chats, activeId, busy, onSelect, onDelete, onNew }: Props) {
  return (
    <aside className="w-full shrink-0 md:w-64" aria-label="Conversations">
      <button type="button" className="btn mb-3 w-full" onClick={onNew} disabled={busy}>New conversation</button>
      {chats.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">No saved conversations yet.</p>
      ) : (
        <ul className="space-y-1">
          {chats.map((c) => (
            <li key={c.id} className="group flex items-center gap-1">
              <button
                type="button"
                disabled={busy}
                onClick={() => onSelect(c.id)}
                aria-current={c.id === activeId ? "true" : undefined}
                className={`min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-left text-sm ${
                  c.id === activeId ? "bg-indigo-600 text-white" : "hover:bg-slate-200 dark:hover:bg-slate-800"
                }`}
                title={c.title}
              >
                {c.title}
              </button>
              <button
                type="button"
                disabled={busy}
                aria-label={`Delete ${c.title}`}
                className="btn-secondary px-2 py-1 text-xs"
                onClick={() => {
                  if (window.confirm(`Delete "${c.title}"? This cannot be undone.`)) onDelete(c.id);
                }}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
```

- [ ] **Step 4: Run to verify the sidebar tests pass**

Run (in `frontend`): `npm test -- src/components/ChatSidebar.test.tsx`
Expected: PASS.

- [ ] **Step 5: Write the failing Ask page test** — `frontend/src/pages/Ask.test.tsx`

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Ask from "./Ask";

const answer = (text: string, chat_id: number) => ({
  answer: text, answerable: true, unverified_numbers: [], charts: [], tables: [], sources: [], sql: [],
  plan: null, steps: [], cache_hit: false, request_id: "r", chat_id, turn_id: 1,
});

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const out = handler(url, init) as { status?: number; body: unknown };
    const status = out.status ?? 200;
    return { ok: status < 400, status, statusText: "", json: async () => out.body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderAsk() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><Ask /></QueryClientProvider>);
}

const bodyOf = (call: unknown[]) => JSON.parse((call[1] as RequestInit).body as string);

describe("Ask page", () => {
  beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });
  afterEach(() => vi.unstubAllGlobals());

  it("starts without chat_id, then continues the same chat", async () => {
    const fetchMock = mockFetch((url) => {
      if (url === "/api/chats") return { body: { items: [], total: 0 } };
      if (url === "/api/query") return { body: answer("first answer", 5) };
      return { status: 404, body: {} };
    });
    renderAsk();
    const input = await screen.findByLabelText(/question/i);
    fireEvent.change(input, { target: { value: "first q" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    expect(await screen.findByText("first answer")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "second q" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await waitFor(() => expect(fetchMock.mock.calls.filter((c) => c[0] === "/api/query")).toHaveLength(2));
    const [first, second] = fetchMock.mock.calls.filter((c) => c[0] === "/api/query").map(bodyOf);
    expect(first.chat_id).toBeUndefined();
    expect(second.chat_id).toBe(5);
    expect(second.history).toBeUndefined();
  });

  it("keeps the conversation when the first question fails", async () => {
    let n = 0;
    const fetchMock = mockFetch((url) => {
      if (url === "/api/chats") return { body: { items: [], total: 0 } };
      n += 1;
      return n === 1
        ? { status: 502, body: { error: { code: "llm_unavailable", message: "The language model is unavailable.", chat_id: 9 } } }
        : { body: answer("recovered", 9) };
    });
    renderAsk();
    const input = await screen.findByLabelText(/question/i);
    fireEvent.change(input, { target: { value: "q1" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    expect(await screen.findByText("The language model is unavailable.")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "q2" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    const queries = fetchMock.mock.calls.filter((c) => c[0] === "/api/query").map(bodyOf);
    expect(queries[1].chat_id).toBe(9);
  });

  it("opens a saved chat and renders its turns, including a failed one", async () => {
    mockFetch((url) => {
      if (url === "/api/chats") return { body: { items: [{ id: 3, title: "Old chat", updated_at: "2026-10-16T00:00:00Z", turn_count: 2 }], total: 1 } };
      if (url === "/api/chats/3")
        return {
          body: {
            id: 3, title: "Old chat", created_at: "2026-10-16T00:00:00Z",
            turns: [
              { id: 1, turn_index: 0, question: "saved q", response: answer("saved answer", 3), status: "ok", error: null, created_at: "x" },
              { id: 2, turn_index: 1, question: "failed q", response: null, status: "timeout", error: "exceeded 150s", created_at: "x" },
            ],
          },
        };
      return { status: 404, body: {} };
    });
    renderAsk();
    fireEvent.click(await screen.findByText("Old chat"));
    expect(await screen.findByText("saved answer")).toBeInTheDocument();
    expect(screen.getByText("failed q")).toBeInTheDocument();
    expect(screen.getByText(/took too long/i)).toBeInTheDocument();
    expect(screen.queryByText(/exceeded 150s/)).not.toBeInTheDocument(); // internal error text is not shown
  });

  it("deleting the open chat resets the page", async () => {
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
    const fetchMock = mockFetch((url, init) => {
      if (url === "/api/chats" ) return { body: { items: [{ id: 3, title: "Old chat", updated_at: "x", turn_count: 1 }], total: 1 } };
      if (url === "/api/chats/3" && init?.method === "DELETE") return { status: 204, body: null };
      if (url === "/api/chats/3")
        return { body: { id: 3, title: "Old chat", created_at: "x", turns: [{ id: 1, turn_index: 0, question: "saved q", response: answer("saved answer", 3), status: "ok", error: null, created_at: "x" }] } };
      return { status: 404, body: {} };
    });
    renderAsk();
    fireEvent.click(await screen.findByText("Old chat"));
    await screen.findByText("saved answer");
    fireEvent.click(screen.getByRole("button", { name: /delete old chat/i }));
    await waitFor(() => expect(screen.queryByText("saved answer")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.some((c) => c[0] === "/api/chats/3" && (c[1] as RequestInit)?.method === "DELETE")).toBe(true);
  });
});
```

- [ ] **Step 6: Run to verify it fails**

Run (in `frontend`): `npm test -- src/pages/Ask.test.tsx`
Expected: FAIL — no sidebar / `chat_id` not sent.

- [ ] **Step 7: Rewrite `frontend/src/pages/Ask.tsx`**

```tsx
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { ChatDetail, ChatList, ChatTurnRecord, QueryResponse } from "../api/types";
import AnswerCard from "../components/AnswerCard";
import ChatSidebar from "../components/ChatSidebar";

const EXAMPLES = [
  "What is the total revenue by region?",
  "Show the monthly revenue trend",
  "Why did Q3 performance drop?",
  "What is the average salary by department?",
  "Show the monthly revenue trend and explain the Q3 dip",
];

const FAILURE_MESSAGES: Record<string, string> = {
  plan_failed: "I couldn't turn this question into a valid analysis plan. Try rephrasing it.",
  llm_quota_exhausted: "The AI provider's quota is exhausted. Ask the administrator to check API billing and limits.",
  llm_unavailable: "The language model was unavailable for this question.",
  timeout: "This question took too long to answer.",
};

interface Turn {
  id: number;
  question: string;
  response?: QueryResponse;
  error?: string;
  loading: boolean;
}

function fromRecord(t: ChatTurnRecord): Turn {
  return {
    id: t.id,
    question: t.question,
    response: t.response ?? undefined,
    error: t.response ? undefined : FAILURE_MESSAGES[t.status] ?? "This question could not be answered.",
    loading: false,
  };
}

export default function Ask() {
  const queryClient = useQueryClient();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [chatId, setChatId] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const busy = turns.some((t) => t.loading);

  const chats = useQuery({ queryKey: ["chats"], queryFn: () => api<ChatList>("/chats") });

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [turns]);

  function newConversation() {
    setChatId(null);
    setTurns([]);
  }

  async function openChat(id: number) {
    if (busy) return;
    try {
      const detail = await api<ChatDetail>(`/chats/${id}`);
      setChatId(detail.id);
      setTurns(detail.turns.map(fromRecord));
    } catch {
      void queryClient.invalidateQueries({ queryKey: ["chats"] });
    }
  }

  async function deleteChat(id: number) {
    if (busy) return;
    await api(`/chats/${id}`, { method: "DELETE" });
    if (id === chatId) newConversation();
    void queryClient.invalidateQueries({ queryKey: ["chats"] });
  }

  async function ask(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const id = Date.now();
    setTurns((ts) => [...ts, { id, question: q, loading: true }]);
    setQuestion("");
    try {
      const response = await api<QueryResponse>("/query", { body: { question: q, ...(chatId !== null && { chat_id: chatId }) } });
      setChatId(response.chat_id);
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, response, loading: false } : t)));
    } catch (err) {
      if (err instanceof ApiError && err.chatId !== undefined) setChatId(err.chatId);
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, error: err instanceof Error ? err.message : "Request failed", loading: false } : t)));
    } finally {
      void queryClient.invalidateQueries({ queryKey: ["chats"] });
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(question);
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 md:flex-row">
      <ChatSidebar
        chats={chats.data?.items ?? []}
        activeId={chatId}
        busy={busy}
        onSelect={(id) => void openChat(id)}
        onDelete={(id) => void deleteChat(id)}
        onNew={newConversation}
      />
      <div className="min-w-0 flex-1">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold">Ask your data</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Questions are routed to SQL, document, compute and chart agents. Every number is checked against the data.</p>
        </div>

        {turns.length === 0 && (
          <div className="card mb-6">
            <p className="mb-3 text-sm font-medium">Try one of these:</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((e) => (
                <button key={e} type="button" className="btn-secondary text-left" onClick={() => void ask(e)}>{e}</button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-6">
          {turns.map((t) => (
            <div key={t.id} className="space-y-3">
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-white">{t.question}</div>
              </div>
              <div className="card">
                {t.loading && <p className="animate-pulse text-sm text-slate-500">Analyzing… planning steps, querying data and checking numbers.</p>}
                {t.error && <p className="error-text" role="alert">{t.error}</p>}
                {t.response && <AnswerCard response={t.response} />}
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>

        <form onSubmit={onSubmit} className="sticky bottom-0 mt-6 flex gap-2 bg-slate-50 py-4 dark:bg-slate-950">
          <label htmlFor="question" className="sr-only">Question</label>
          <input
            id="question"
            className="input"
            placeholder="e.g. Which product category grew fastest in Q4?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={2000}
          />
          <button type="submit" className="btn shrink-0" disabled={busy || !question.trim()}>Ask</button>
        </form>
      </div>
    </div>
  );
}
```

The old "New conversation" header button is replaced by the sidebar's. The old client-built `history` is gone: the server builds context from the chat.

- [ ] **Step 8: Run all frontend checks**

Run (in `frontend`): `npm test` then `npm run build`
Expected: all tests (including the existing Login and DataTable tests) pass; `tsc --noEmit` passes. If the "failed turn" assertion `getByText(/took too long/i)` fails, the `timeout` entry in `FAILURE_MESSAGES` is missing/misspelled — fix the map, not the test.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/ChatSidebar.tsx frontend/src/components/ChatSidebar.test.tsx frontend/src/pages/Ask.tsx frontend/src/pages/Ask.test.tsx
git commit -m "feat(frontend): saved conversations sidebar on the Ask page"
```

---

### Task 6: Docs and end-to-end check

**Files:**
- Modify: `README.md` (project structure / features section), spec addendum
- Modify: `docs/superpowers/specs/2026-10-17-chat-history-design.md` (one bullet)

- [ ] **Step 1: Document the feature** — in `README.md`, add under the features/how-it-works section:

```markdown
### Chat history

Every question is saved to Postgres (`app.chats`, `app.chat_turns`) under the signed-in user. The Ask page lists
your conversations; open one to continue it, or delete it. Follow-up questions use the last 3 answered turns of the
open chat as context. Admins cannot read other users' chats (they keep the query audit under Admin).
```

- [ ] **Step 2: Record the error-body addition in the spec** — in the spec's "API" section add a bullet: `Error responses from POST /api/query include "chat_id" inside "error" when a chat exists, so a conversation whose first turn failed can continue.` and in "Frontend behaviour" change "failed turns show their saved error" to "failed turns show a friendly message chosen from the saved status (the raw internal error is stored but not displayed)".

- [ ] **Step 3: Full verification**

Run: `run-tests.bat tests -q` (whole backend suite; live tests are excluded by default) and, in `frontend`, `npm test` and `npm run build`.
Expected: everything green. Then manual check: `run-backend.bat` and `run-frontend.bat`, sign in, ask two questions, refresh the page, open the chat from the sidebar and confirm both turns render; delete it and confirm it disappears; sign in as a second user and confirm the first user's chats are not listed.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-17-chat-history-design.md
git commit -m "docs: chat history feature and spec addenda"
```
