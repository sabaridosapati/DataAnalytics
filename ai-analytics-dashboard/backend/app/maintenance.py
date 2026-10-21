from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def purge_structured_data(engine: AsyncEngine, apply: bool) -> dict:
    """Remove structured data that used to live in Postgres (data.* tables + their dataset rows).

    Dry-run by default (apply=False). Documents, chunks, users, chats, audit and pins are never touched.
    """
    async with engine.begin() as conn:
        tables = list((await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'data' ORDER BY 1"))).scalars())
        rows = (await conn.execute(text("SELECT id FROM app.datasets WHERE kind = 'table'"))).all()
        if apply:
            for t in tables:
                await conn.execute(text('DROP TABLE IF EXISTS data."' + t.replace('"', '""') + '"'))
            await conn.execute(text("DELETE FROM app.datasets WHERE kind = 'table'"))
            await conn.execute(text("UPDATE app.catalog_version SET version = version + 1"))
    return {"applied": apply, "dropped_tables": tables, "deleted_dataset_rows": len(rows)}
