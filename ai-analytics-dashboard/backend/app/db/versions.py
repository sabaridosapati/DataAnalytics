from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_catalog_version(session: AsyncSession) -> int:
    value = await session.scalar(text("SELECT version FROM app.catalog_version WHERE id = 1"))
    return int(value or 1)


async def bump_catalog_version(session: AsyncSession) -> None:
    """Invalidate catalog/answer caches. The caller commits."""
    await session.execute(text("UPDATE app.catalog_version SET version = version + 1 WHERE id = 1"))
