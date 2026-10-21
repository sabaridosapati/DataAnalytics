from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.duckdb_store import upload_db_path


async def purge_structured_data(engine: AsyncEngine, apply: bool, duckdb_dir) -> dict:
    """Remove structured data that used to live in Postgres (data.* tables + their dataset rows).

    Table dataset rows whose upload has a DuckDB file under ``duckdb_dir`` are current datasets and are kept;
    only legacy rows without a file are deleted. Dry-run by default (apply=False).
    Documents, chunks, users, chats, audit and pins are never touched.
    """
    async with engine.begin() as conn:
        tables = list((await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'data' ORDER BY 1"))).scalars())
        rows = (await conn.execute(text("SELECT id, slug, parent_upload_id FROM app.datasets WHERE kind = 'table' ORDER BY id"))).all()
        legacy = [r for r in rows if not upload_db_path(Path(duckdb_dir), r.parent_upload_id or r.id).exists()]
        if apply:
            for t in tables:
                await conn.execute(text('DROP TABLE IF EXISTS data."' + t.replace('"', '""') + '"'))
            if legacy:
                await conn.execute(
                    text("DELETE FROM app.datasets WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
                    {"ids": [r.id for r in legacy]},
                )
            await conn.execute(text("UPDATE app.catalog_version SET version = version + 1"))
    return {
        "applied": apply,
        "dropped_tables": tables,
        "deleted_dataset_rows": len(legacy),
        "deleted_dataset_slugs": [r.slug for r in legacy],
        "kept_dataset_rows": len(rows) - len(legacy),
    }
