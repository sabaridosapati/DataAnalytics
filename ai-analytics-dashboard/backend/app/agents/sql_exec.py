import asyncpg

from app.cache.redis_cache import sql_key
from app.util import to_jsonable


class QueryExecutionError(Exception):
    pass


async def run_readonly_query(state, sql: str, version: int, settings) -> dict:
    """Run already-validated SQL as the query_ro role inside a read-only transaction (cached)."""
    key = sql_key(version, sql)
    cached = await state.cache.get_json(key)
    if cached is not None:
        return {**cached, "cache_hit": True}
    try:
        async with state.ro_pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                stmt = await conn.prepare(sql, timeout=settings.step_timeout_s)
                records = await stmt.fetch(timeout=settings.step_timeout_s)
                columns = [a.name for a in stmt.get_attributes()]
    except asyncpg.PostgresError as exc:
        raise QueryExecutionError(f"{type(exc).__name__}: {exc}") from exc
    rows = [[to_jsonable(v) for v in r.values()] for r in records]
    result = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": len(rows) >= settings.max_sql_rows}
    await state.cache.set_json(key, result, settings.cache_ttl_s)
    return {**result, "cache_hit": False}
