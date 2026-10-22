import shutil
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
                "TRUNCATE app.chat_turns, app.chats, app.query_audit, app.dashboard_pins, app.dataset_columns, app.email_codes, "
                "vector.chunks, app.datasets, app.users RESTART IDENTITY CASCADE"
            )
        )
        await conn.execute(text("UPDATE app.catalog_version SET version = version + 1"))
    r = aioredis.from_url(get_settings().redis_url)
    await r.flushdb()
    await r.aclose()
    shutil.rmtree(get_settings().duckdb_path, ignore_errors=True)
    get_settings().duckdb_path.mkdir(parents=True, exist_ok=True)


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
