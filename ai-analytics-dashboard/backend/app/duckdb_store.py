"""DuckDB storage and query helpers for uploaded structured data.

One file per upload (``upload_<id>.duckdb``) holds every table produced by that upload under schema ``main``.
Everything here is synchronous and meant to run in a worker thread (``asyncio.to_thread``).
"""
import logging
import os
import re
import threading
from pathlib import Path

import duckdb
import pandas as pd

log = logging.getLogger(__name__)


class DuckDBError(Exception):
    """A DuckDB operation failed."""


class DuckDBTimeout(DuckDBError):
    """The query exceeded its time budget and was interrupted."""


class DuckDBMissingFile(DuckDBError):
    """A dataset's .duckdb file does not exist on disk."""


def upload_db_path(duckdb_dir, upload_id: int) -> Path:
    return Path(duckdb_dir) / f"upload_{int(upload_id)}.duckdb"


def _ident(name: str) -> str:
    if "\x00" in name:
        raise ValueError("Invalid identifier")
    return '"' + name.replace('"', '""') + '"'


def _lit(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


_LEADING_NOISE = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/|\()+", re.S)
_SELECT_START = re.compile(r"(?:select|with|from)\b", re.I)


def _require_single_select(con, sql: str) -> None:
    """Refuse anything but exactly one real SELECT (DuckDB runs multi-statement strings, and rewrites PRAGMAs to SELECTs)."""
    msg = "Exactly one SELECT statement is allowed."
    try:
        stmts = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise DuckDBError(f"{type(exc).__name__}: {exc}") from exc
    if len(stmts) != 1 or stmts[0].type != duckdb.StatementType.SELECT:
        raise DuckDBError(msg)
    if not _SELECT_START.match(_LEADING_NOISE.sub("", sql, count=1)):
        raise DuckDBError(msg)


def _tmp(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


def _unlink_with_wal(path: Path) -> None:
    for p in (path, path.with_name(path.name + ".wal")):
        p.unlink(missing_ok=True)


def _select_expr(col: str, duck_type: str) -> str:
    c = _ident(col)
    if duck_type == "DOUBLE":
        return f"CASE WHEN isfinite({c}) THEN CAST({c} AS DOUBLE) END AS {c}"
    return f"CAST({c} AS {duck_type}) AS {c}"


def write_upload_file(path: Path, tables: list[tuple[str, pd.DataFrame, dict[str, str]]]) -> dict[str, int]:
    """Write all tables of one upload into ``path`` atomically (temp file + rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    _unlink_with_wal(tmp)
    counts: dict[str, int] = {}
    try:
        con = duckdb.connect(str(tmp))
        try:
            for slug, df, types in tables:
                cols = ", ".join(f"{_ident(c)} {types[c]}" for c in df.columns)
                con.execute(f"CREATE TABLE main.{_ident(slug)} ({cols})")
                if len(df):
                    con.register("src_df", df)
                    select = ", ".join(_select_expr(c, types[c]) for c in df.columns)
                    con.execute(f"INSERT INTO main.{_ident(slug)} SELECT {select} FROM src_df")
                    con.unregister("src_df")
                counts[slug] = con.execute(f"SELECT COUNT(*) FROM main.{_ident(slug)}").fetchone()[0]
        finally:
            con.close()
        os.replace(tmp, path)
    except BaseException:
        _unlink_with_wal(tmp)
        raise
    return counts


def preview_table(path: Path, slug: str, limit: int = 20) -> tuple[list[str], list[tuple]]:
    path = Path(path)
    if not path.exists():
        raise DuckDBMissingFile("The dataset file is missing; please upload the data again.")
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            cur = con.execute(f"SELECT * FROM main.{_ident(slug)} LIMIT {int(limit)}")
            return [d[0] for d in cur.description], cur.fetchall()
        finally:
            con.close()
    except duckdb.Error as exc:
        raise DuckDBError(f"{type(exc).__name__}: {exc}") from exc


def run_select(
    files: dict[int, Path],
    tables: dict[str, int],
    sql: str,
    *,
    max_rows: int,
    timeout_s: float,
    memory_limit: str = "512MB",
    threads: int = 2,
) -> dict:
    """Run one validated SELECT on a fresh, locked-down in-memory DuckDB connection.

    ``files`` maps upload id -> .duckdb path; ``tables`` maps slug -> upload id and becomes ``data.<slug>`` views.
    """
    for p in files.values():
        if not Path(p).exists():
            raise DuckDBMissingFile("A dataset file is missing; please upload the data again.")
    timed_out = threading.Event()
    timer: threading.Timer | None = None
    con = duckdb.connect(":memory:")
    try:
        try:
            _require_single_select(con, sql)
            for upload_id, p in files.items():
                con.execute(f"ATTACH {_lit(Path(p))} AS up_{int(upload_id)} (READ_ONLY)")
            con.execute("CREATE SCHEMA data")
            for slug, upload_id in tables.items():
                con.execute(f"CREATE VIEW data.{_ident(slug)} AS SELECT * FROM up_{int(upload_id)}.main.{_ident(slug)}")
            con.execute(f"SET memory_limit = {_lit(memory_limit)}")
            con.execute(f"SET threads = {int(threads)}")
            con.execute("SET python_enable_replacements = false")
            con.execute("SET enable_external_access = false")
            con.execute("SET lock_configuration = true")

            def _interrupt():
                timed_out.set()
                con.interrupt()

            timer = threading.Timer(float(timeout_s), _interrupt)
            timer.daemon = True
            timer.start()
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(int(max_rows))
            return {"columns": columns, "rows": [list(r) for r in rows], "truncated": len(rows) >= int(max_rows)}
        except (duckdb.Error, OSError) as exc:
            if timed_out.is_set() or isinstance(exc, duckdb.InterruptException):
                raise DuckDBTimeout(f"The query took longer than {timeout_s:g}s and was stopped.") from exc
            raise DuckDBError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if timer is not None:
            timer.cancel()
        con.close()


def remove_upload_file(path: Path) -> None:
    """Delete a .duckdb file (and its WAL). Raises PermissionError if it is open (Windows)."""
    _unlink_with_wal(Path(path))


def cleanup_stale_tmp(duckdb_dir) -> int:
    """Remove leftover ``*.tmp`` / ``*.tmp.wal`` files from interrupted writes."""
    d = Path(duckdb_dir)
    if not d.is_dir():
        return 0
    removed = 0
    for f in d.iterdir():
        if f.name.endswith(".tmp") or f.name.endswith(".tmp.wal"):
            f.unlink(missing_ok=True)
            removed += 1
    return removed
