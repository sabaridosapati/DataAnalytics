# Task 2: DB models, session, security primitives, admin seed

**Files:**
- Create: `backend/app/db/__init__.py` (empty), `backend/app/db/models.py`, `backend/app/db/session.py`, `backend/app/db/seed.py`, `backend/app/db/versions.py`
- Create: `backend/app/auth/__init__.py` (empty), `backend/app/auth/security.py`
- Test: `backend/tests/unit/test_security.py`, `backend/tests/helpers.py`, `backend/tests/integration/conftest.py`, `backend/tests/integration/test_db.py`

**Interfaces:**
- Consumes: `app.config.Settings`.
- Produces (models, all in `app.db.models`): `Base`, `User`, `EmailCode`, `Dataset`, `DatasetColumn`, `QueryAudit`, `DashboardPin`, `CatalogVersion`, `Chunk` — columns exactly as defined below.
- Produces: `create_engine_and_sessionmaker(url) -> (AsyncEngine, async_sessionmaker)`, `async wait_for_db(engine)`, `async init_db(engine)`.
- Produces: `async seed_admin(sessionmaker, settings) -> None` (idempotent).
- Produces: `async get_catalog_version(session) -> int`, `async bump_catalog_version(session) -> None` (caller commits).
- Produces (`app.auth.security`): `hash_password(str)->str`, `verify_password(str,str)->bool`, `password_problem(str)->str|None`, `create_access_token(user_id:int, role:str, secret:str, minutes:int)->str`, `decode_access_token(token, secret)->dict`, `generate_code()->str`, `hash_code(code, user_id, secret)->str`.
- Produces (test infra): fixture `db` → `SimpleNamespace(engine, sessionmaker)` with a clean database; fixture `client` (httpx AsyncClient over the app, with `client.app`); fixture `admin_token`; helpers `auth`, `unique_email`, `mailpit_code`, `register_verified_user`, `upload_and_wait`.

- [ ] **Step 1: Write the failing security unit tests**

**File: `backend/tests/unit/test_security.py`**
```python
import jwt
import pytest

from app.auth.security import (
    create_access_token,
    decode_access_token,
    generate_code,
    hash_code,
    hash_password,
    password_problem,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("Passw0rd!")
    assert h != "Passw0rd!"
    assert verify_password("Passw0rd!", h)
    assert not verify_password("wrong", h)


def test_verify_password_bad_hash_is_false():
    assert verify_password("x", "not-a-bcrypt-hash") is False


@pytest.mark.parametrize(
    "pw, ok",
    [("short1", False), ("allletters", False), ("12345678", False), ("Passw0rd", True), ("Test@123", True), ("a1" * 40, False)],
)
def test_password_problem(pw, ok):
    assert (password_problem(pw) is None) is ok


def test_token_roundtrip_and_expiry():
    token = create_access_token(7, "admin", "s3cret", 5)
    payload = decode_access_token(token, "s3cret")
    assert payload["sub"] == "7" and payload["role"] == "admin"
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token, "other")
    expired = create_access_token(7, "admin", "s3cret", -1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired, "s3cret")


def test_codes():
    code = generate_code()
    assert len(code) == 6 and code.isdigit()
    assert hash_code("123456", 1, "k") == hash_code("123456", 1, "k")
    assert hash_code("123456", 1, "k") != hash_code("123456", 2, "k")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit/test_security.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 3: Implement security primitives**

**File: `backend/app/auth/__init__.py`**
```python
```

**File: `backend/app/auth/security.py`**
```python
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def password_problem(password: str) -> str | None:
    """Return a human-readable problem with the password, or None if acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password.encode("utf-8")) > 72:
        return "Password must be at most 72 bytes."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain at least one letter and one digit."
    return None


def create_access_token(user_id: int, role: str, secret: str, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"])


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str, user_id: int, secret: str) -> str:
    return hmac.new(secret.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()
```

- [ ] **Step 4: Run security tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit/test_security.py -q`
Expected: all pass.

- [ ] **Step 5: Write integration test infrastructure and failing DB tests**

**File: `backend/tests/helpers.py`**
```python
import asyncio
import os
import re
import uuid
from pathlib import Path

import httpx

MAILPIT = os.environ.get("MAILPIT_API", "http://mailpit:8025")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def unique_email() -> str:
    return f"user_{uuid.uuid4().hex[:10]}@example.com"


async def mailpit_code(email: str, timeout_s: float = 10.0) -> str:
    """Return the 6-digit code from the newest email sent to `email`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    async with httpx.AsyncClient(base_url=MAILPIT, timeout=10) as m:
        while True:
            r = await m.get("/api/v1/search", params={"query": f'to:"{email}"'})
            r.raise_for_status()
            msgs = r.json().get("messages") or []
            if msgs:
                detail = (await m.get(f"/api/v1/message/{msgs[0]['ID']}")).json()
                found = re.search(r"\b(\d{6})\b", detail.get("Text", ""))
                if found:
                    return found.group(1)
            if loop.time() > deadline:
                raise AssertionError(f"No verification email found for {email}")
            await asyncio.sleep(0.3)


async def register_verified_user(client, password: str = "Passw0rd!") -> tuple[str, str]:
    email = unique_email()
    r = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    code = await mailpit_code(email)
    r = await client.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return email, r.json()["access_token"]


async def upload_and_wait(client, token: str, paths: list[Path], timeout_s: float = 90.0) -> list[dict]:
    """Upload files and wait until they (and any child datasets) are ready/failed."""
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    r = await client.post("/api/datasets/upload", files=files, headers=auth(token))
    assert r.status_code == 202, r.text
    ids = {d["id"] for d in r.json()}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        listing = (await client.get("/api/datasets", headers=auth(token))).json()
        mine = [d for d in listing if d["id"] in ids or d.get("parent_upload_id") in ids]
        if mine and all(d["status"] in ("ready", "failed") for d in mine):
            return mine
        if loop.time() > deadline:
            raise AssertionError(f"Datasets not ready in time: {mine}")
        await asyncio.sleep(0.3)
```

**File: `backend/tests/integration/conftest.py`**
```python
from types import SimpleNamespace

import httpx
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import text

from app.config import get_settings


async def reset_database(engine) -> None:
    async with engine.begin() as conn:
        tables = (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'data'"))).scalars().all()
        for t in tables:
            await conn.execute(text(f'DROP TABLE IF EXISTS data."{t}"'))
        await conn.execute(
            text(
                "TRUNCATE app.query_audit, app.dashboard_pins, app.dataset_columns, app.email_codes, "
                "vector.chunks, app.datasets, app.users RESTART IDENTITY CASCADE"
            )
        )
        await conn.execute(text("UPDATE app.catalog_version SET version = version + 1"))
    r = aioredis.from_url(get_settings().redis_url)
    await r.flushdb()
    await r.aclose()


@pytest_asyncio.fixture
async def db():
    from app.db.session import create_engine_and_sessionmaker, init_db

    engine, sessionmaker = create_engine_and_sessionmaker(get_settings().database_url)
    await init_db(engine)
    await reset_database(engine)
    yield SimpleNamespace(engine=engine, sessionmaker=sessionmaker)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    from asgi_lifespan import LifespanManager

    from app.main import create_app

    app = create_app()
    async with LifespanManager(app, startup_timeout=60, shutdown_timeout=30):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as c:
            c.app = app
            yield c


@pytest_asyncio.fixture
async def admin_token(client) -> str:
    r = await client.post("/api/auth/login", json={"identifier": "admin", "password": "Test@123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]
```

**File: `backend/tests/integration/test_db.py`**
```python
import asyncpg
import pytest
from sqlalchemy import select, text

from app.auth.security import verify_password
from app.config import get_settings
from app.db.models import User
from app.db.seed import seed_admin
from app.db.versions import bump_catalog_version, get_catalog_version


async def test_tables_created(db):
    async with db.engine.connect() as c:
        rows = await c.execute(
            text(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema IN ('app', 'vector')"
            )
        )
        names = set(rows.scalars())
    expected = {
        "app.users", "app.email_codes", "app.datasets", "app.dataset_columns",
        "app.query_audit", "app.dashboard_pins", "app.catalog_version", "vector.chunks",
    }
    assert expected <= names


async def test_seed_admin_idempotent(db):
    settings = get_settings()
    await seed_admin(db.sessionmaker, settings)
    await seed_admin(db.sessionmaker, settings)
    async with db.sessionmaker() as s:
        admins = (await s.scalars(select(User).where(User.username == "admin"))).all()
    assert len(admins) == 1
    admin = admins[0]
    assert admin.role == "admin" and admin.is_verified and admin.is_active
    assert admin.email == "admin@analytics.local"
    assert verify_password("Test@123", admin.password_hash)


async def test_catalog_version_bump(db):
    async with db.sessionmaker() as s:
        v1 = await get_catalog_version(s)
        await bump_catalog_version(s)
        await s.commit()
        assert await get_catalog_version(s) == v1 + 1


async def test_query_ro_role_is_locked_down(db):
    conn = await asyncpg.connect(get_settings().query_ro_url)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM app.users")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM vector.chunks")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("CREATE TABLE data.evil (id int)")
    finally:
        await conn.close()
```

- [ ] **Step 6: Run DB tests to verify they fail**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`.

- [ ] **Step 7: Implement models, session, versions, seed**

**File: `backend/app/db/__init__.py`**
```python
```

**File: `backend/app/db/models.py`**
```python
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Computed, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 1536


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailCode(Base):
    __tablename__ = "email_codes"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id", ondelete="CASCADE"), unique=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="pending")  # pending | table | document
    source_filename: Mapped[str] = mapped_column(String(300))
    file_type: Mapped[str] = mapped_column(String(16))
    stored_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_upload_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("app.users.id", ondelete="SET NULL"), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processing|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetColumn(Base):
    __tablename__ = "dataset_columns"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("app.datasets.id", ondelete="CASCADE"), index=True)
    column_name: Mapped[str] = mapped_column(String(80))
    pg_type: Mapped[str] = mapped_column(String(32))
    sample_values: Mapped[list] = mapped_column(JSONB, default=list)
    description: Mapped[str] = mapped_column(Text, default="")


class QueryAudit(Base):
    __tablename__ = "query_audit"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app.users.id", ondelete="SET NULL"), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    plan_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sql_executed: Mapped[list] = mapped_column(JSONB, default=list)
    agents_used: Mapped[list] = mapped_column(JSONB, default=list)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DashboardPin(Base):
    __tablename__ = "dashboard_pins"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    chart: Mapped[dict] = mapped_column(JSONB)
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app.users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogVersion(Base):
    __tablename__ = "catalog_version"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(BigInteger, default=1)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        {"schema": "vector"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("app.datasets.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    embedding = mapped_column(Vector(EMBED_DIM))
    content_tsv = mapped_column(TSVECTOR, Computed("to_tsvector('english', content)", persisted=True))
```

**File: `backend/app/db/session.py`**
```python
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.models import Base

log = logging.getLogger(__name__)


def create_engine_and_sessionmaker(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def wait_for_db(engine: AsyncEngine, attempts: int = 30, delay: float = 1.0) -> None:
    for i in range(attempts):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - retry any connection error
            log.info("Waiting for database (%s/%s): %s", i + 1, attempts, exc)
            await asyncio.sleep(delay)
    raise RuntimeError("Database is not reachable")


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("INSERT INTO app.catalog_version (id, version) VALUES (1, 1) ON CONFLICT (id) DO NOTHING")
        )
```

**File: `backend/app/db/versions.py`**
```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_catalog_version(session: AsyncSession) -> int:
    value = await session.scalar(text("SELECT version FROM app.catalog_version WHERE id = 1"))
    return int(value or 1)


async def bump_catalog_version(session: AsyncSession) -> None:
    """Invalidate catalog/answer caches. The caller commits."""
    await session.execute(text("UPDATE app.catalog_version SET version = version + 1 WHERE id = 1"))
```

**File: `backend/app/db/seed.py`**
```python
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.security import hash_password
from app.config import Settings
from app.db.models import User

log = logging.getLogger(__name__)


async def seed_admin(sessionmaker: async_sessionmaker, settings: Settings) -> None:
    async with sessionmaker() as session:
        existing = await session.scalar(select(User).where(User.username == "admin"))
        if existing:
            return
        session.add(
            User(
                email=settings.admin_email.lower(),
                username="admin",
                password_hash=hash_password(settings.admin_password),
                role="admin",
                is_verified=True,
                is_active=True,
            )
        )
        await session.commit()
        log.info("Seeded default admin user 'admin'")
```

- [ ] **Step 8: Run DB tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_db.py tests/unit -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend
git commit -m "feat(db): models, session, admin seed, security primitives"
```
