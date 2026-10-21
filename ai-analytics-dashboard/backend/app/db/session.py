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
