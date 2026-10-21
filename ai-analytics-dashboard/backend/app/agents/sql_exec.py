import asyncio

import sqlglot
from sqlglot import exp

from app.cache.redis_cache import sql_key
from app.catalog import load_catalog
from app.duckdb_store import DuckDBError, run_select, upload_db_path
from app.util import to_jsonable


class QueryExecutionError(Exception):
    pass


def referenced_tables(sql: str) -> list[str]:
    """Slugs of the data.<table> references in already-validated SQL."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    return sorted({t.name for t in tree.find_all(exp.Table) if (t.db or "").lower() == "data"})


async def run_readonly_query(state, sql: str, version: int, settings) -> dict:
    """Run already-validated SQL in a locked-down in-memory DuckDB that attaches the needed upload files read-only (cached)."""
    key = sql_key(version, sql)
    cached = await state.cache.get_json(key)
    if cached is not None:
        return {**cached, "cache_hit": True}
    catalog = await load_catalog(state)
    tables: dict[str, int] = {}
    for slug in referenced_tables(sql):
        info = catalog.get(slug)
        if info is None or info.kind != "table":
            raise QueryExecutionError(f"Unknown table '{slug}'.")
        tables[slug] = info.upload_id
    files = {uid: upload_db_path(settings.duckdb_path, uid) for uid in set(tables.values())}
    try:
        out = await asyncio.to_thread(
            run_select, files, tables, sql,
            max_rows=settings.max_sql_rows, timeout_s=settings.step_timeout_s,
            memory_limit=settings.duckdb_memory_limit, threads=settings.duckdb_threads,
        )
    except DuckDBError as exc:
        raise QueryExecutionError(str(exc)) from exc
    rows = [[to_jsonable(v) for v in r] for r in out["rows"]]
    result = {"columns": out["columns"], "rows": rows, "row_count": len(rows), "truncated": out["truncated"]}
    await state.cache.set_json(key, result, settings.cache_ttl_s)
    return {**result, "cache_hit": False}