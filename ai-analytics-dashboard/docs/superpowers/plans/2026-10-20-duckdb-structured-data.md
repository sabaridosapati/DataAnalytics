# DuckDB for Structured Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store every structured upload (CSV/TSV/PSV, Excel sheets, JSON arrays, tables found in PDFs) as a per-upload `.duckdb` file and run all analytical SQL in DuckDB; Postgres no longer stores or queries uploaded rows.

**Architecture:** A new synchronous module `app/duckdb_store.py` writes `upload_<id>.duckdb` files (atomic temp-file + rename) and runs SELECTs on a fresh, locked-down in-memory DuckDB connection that attaches the needed files read-only and exposes each table as `data.<slug>` views. The existing pandas parse/clean/type-infer pipeline is kept; only its final "load into Postgres" step is replaced. The sqlglot validator moves to the DuckDB dialect. The registry (`app.datasets`, `app.dataset_columns`) stays in Postgres.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLAlchemy 2 async + asyncpg (registry/chats/users), `duckdb` (new), pandas, sqlglot, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-20-duckdb-structured-data-design.md`

## Global Constraints

- Postgres keeps: `users`, `email_codes`, `chats`, `chat_turns`, `datasets`, `dataset_columns`, `query_audit`, `dashboard_pins`, `catalog_version`, and `vector.chunks` (documents/embeddings are untouched). **No uploaded row data in Postgres.**
- One DuckDB file per upload: `<settings.duckdb_path>/upload_<U>.duckdb`, tables as `main."<slug>"`. `U = dataset.parent_upload_id or dataset.id`. Default `duckdb_path` = `<UPLOAD_DIR>/duckdb`.
- Writes are atomic: temp file `upload_<U>.duckdb.tmp` renamed on success; failure leaves no file.
- `app.dataset_columns.pg_type` keeps its name (no migration) but stores DuckDB type names: `BIGINT`, `DOUBLE`, `BOOLEAN`, `DATE`, `TIMESTAMP`, `VARCHAR`.
- The SQL agent keeps writing `data.<slug>`; the validator keeps: exactly one statement, SELECT/set-ops only, forbidden node types, schema `data` only, known tables/columns, LIMIT clamp to `max_sql_rows`.
- Query connection hardening order: `ATTACH … (READ_ONLY)` → `CREATE SCHEMA data` + views → `SET memory_limit` → `SET threads` → `SET enable_external_access = false` → `SET lock_configuration = true`. Timeout via `threading.Timer` calling `connection.interrupt()` after `settings.step_timeout_s`.
- Defaults: `duckdb_memory_limit="512MB"`, `duckdb_threads=2`.
- `run_readonly_query(state, sql, version, settings)` keeps its signature, Redis caching and return shape `{columns, rows, row_count, truncated, cache_hit}`.
- Keep `settings.query_ro_url`, the `query_ro` DB role and `tests/integration/test_db.py::test_query_ro_role_is_locked_down` unchanged (the role is simply no longer used by the app). **Spec amendment:** the spec says to drop `QUERY_RO_URL` plumbing; this plan keeps it, because the role test still guards the DB init script and removal buys nothing. Task 7 amends the spec.
- The purge script never runs automatically; dry-run is the default.
- Commits: stage **explicit paths only** (the tree has unrelated uncommitted changes). Repo root is `C:\FDE_Practice\project03` (project folder `ai-analytics-dashboard/` inside it). End every commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- The integration suite is **expected to be partially red between Task 3 and Task 5** (ingestion writes DuckDB before the query path/tests are switched). Each task's own listed tests must pass; Task 5 restores the full suite to green.

### Running tests (no image build)

Docker postgres/redis/mailpit must be running (`docker compose up -d --no-build postgres redis mailpit`). From the project folder in **PowerShell**: `.\run-tests.bat tests\unit\test_x.py -q` (the runner is `run-tests.bat`; from Git Bash it only prints a banner). Unit tests do not need Postgres but run through the same script.

## Review Focus

1. SQL that reads files or escapes the sandbox (`FROM read_csv('/etc/passwd')`, `FROM 'x.csv'`, `glob`, `getenv`, `COPY`, `ATTACH`, `INSTALL`, `PRAGMA`, `SET enable_external_access=true`) is rejected by the validator **and** blocked by the locked connection (Task 1 and Task 2 tests).
2. Many concurrent queries on the same upload file all succeed (Task 1 spike test, 8 threads).
3. A write failure midway (bad type, exception after rename, DB error while registering) leaves no `.duckdb`/`.tmp` file and no ready dataset rows (Tasks 1 and 3).
4. Deleting a dataset whose file is in use returns 409 `busy` and keeps the dataset (Task 4).
5. A dataset file deleted from disk behind the app's back gives a clear step error, not a crash (Task 4).
6. NaN/inf/NULL values, header-only/empty frames, reserved-word and mixed-case column names, and slugs needing quotes round-trip correctly (Task 1).
7. A runaway query is actually interrupted by the timeout (Task 1).
8. On Windows, files are closed after every call so rename/delete work (Tasks 1, 3, 4).

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/duckdb_store.py` (create) | write upload files, preview, run locked read-only SELECT, remove files, stale-tmp cleanup |
| `backend/app/config.py` (modify) | `duckdb_dir`, `duckdb_memory_limit`, `duckdb_threads`, `duckdb_path` property |
| `backend/requirements.txt`, `run-tests.bat`, `run-backend.bat` (modify) | add `duckdb`; make runners install it |
| `backend/app/guardrails/sql_validator.py` (modify) | DuckDB dialect + table-function/file-path/system-relation rules |
| `backend/app/agents/sql_agent.py`, `backend/app/llm/demo_script.py` (modify) | DuckDB prompt, DuckDB-compatible demo SQL |
| `backend/app/ingestion/tabular.py` (modify) | DuckDB type labels; delete the Postgres COPY loader |
| `backend/app/ingestion/pipeline.py` (modify) | write one DuckDB file per upload, then register datasets |
| `backend/app/catalog.py` (modify) | `DatasetInfo.upload_id` |
| `backend/app/agents/sql_exec.py` (modify) | run SQL in DuckDB |
| `backend/app/api/datasets.py`, `backend/app/main.py` (modify) | preview/delete via DuckDB; drop `ro_pool`; startup tmp cleanup + dir creation |
| `backend/app/maintenance.py`, `backend/scripts/purge_postgres_data.py` (create) | purge old structured data from Postgres |
| tests | see each task |

---

### Task 1: DuckDB store module, settings, dependency (includes the concurrency spike)

**Files:**
- Modify: `backend/requirements.txt`, `backend/app/config.py`, `run-tests.bat`, `run-backend.bat`
- Create: `backend/app/duckdb_store.py`
- Test: `backend/tests/unit/test_duckdb_store.py`

**Interfaces:**
- Produces (used by Tasks 3, 4, 5, 6):
  - `settings.duckdb_path: Path`, `settings.duckdb_memory_limit: str`, `settings.duckdb_threads: int`
  - `upload_db_path(duckdb_dir, upload_id: int) -> Path`
  - `write_upload_file(path: Path, tables: list[tuple[str, pd.DataFrame, dict[str, str]]]) -> dict[str, int]` — `(slug, df, types)` → `{slug: row_count}`; atomic; types are DuckDB type names
  - `preview_table(path: Path, slug: str, limit: int = 20) -> tuple[list[str], list[tuple]]`
  - `run_select(files: dict[int, Path], tables: dict[str, int], sql: str, *, max_rows: int, timeout_s: float, memory_limit: str = "512MB", threads: int = 2) -> dict` returning `{"columns": list[str], "rows": list[list], "truncated": bool}` with raw Python values
  - `remove_upload_file(path: Path) -> None` (deletes file and `.wal`; may raise `PermissionError`)
  - `cleanup_stale_tmp(duckdb_dir) -> int`
  - Exceptions `DuckDBError`, `DuckDBTimeout(DuckDBError)`, `DuckDBMissingFile(DuckDBError)`

**STOP condition (spike):** if `test_concurrent_read_only_attach_on_one_file` cannot be made to pass, do **not** invent a workaround: report BLOCKED with the exact error so the controller can switch to the spec's fallback (process-wide read-only connection per file).

- [ ] **Step 1: Install the dependency and update runners**

In `backend/requirements.txt` add the line `duckdb>=1.1,<2` (after `pandas`). In `run-tests.bat` change the import check line to
`.venv\Scripts\python.exe -c "import pytest, asgi_lifespan, duckdb" 2>nul || .venv\Scripts\python.exe -m pip install -r requirements-dev.txt || exit /b 1`.
In `run-backend.bat` replace the venv-creation block with:

```bat
if not exist ".venv\Scripts\python.exe" (
  echo Creating backend\.venv ...
  python -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -c "import duckdb, fastapi" 2>nul || (
  echo Installing backend requirements ...
  .venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
)
```

Run (PowerShell, project folder): `backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt` and confirm `backend\.venv\Scripts\python.exe -c "import duckdb; print(duckdb.__version__)"` prints a version.

- [ ] **Step 2: Add settings** — in `backend/app/config.py` add `from pathlib import Path` to the imports, these fields after `upload_dir`:

```python
    duckdb_dir: str = ""  # default: <upload_dir>/duckdb
    duckdb_memory_limit: str = "512MB"
    duckdb_threads: int = 2
```

and this property next to `cors_origin_list`:

```python
    @property
    def duckdb_path(self) -> Path:
        return Path(self.duckdb_dir) if self.duckdb_dir else Path(self.upload_dir) / "duckdb"
```

- [ ] **Step 3: Write the failing tests** — `backend/tests/unit/test_duckdb_store.py`

```python
import threading
import time

import duckdb
import numpy as np
import pandas as pd
import pytest

from app.duckdb_store import (
    DuckDBError,
    DuckDBMissingFile,
    DuckDBTimeout,
    cleanup_stale_tmp,
    preview_table,
    remove_upload_file,
    run_select,
    upload_db_path,
    write_upload_file,
)

SALES_TYPES = {"region": "VARCHAR", "revenue": "DOUBLE", "qty": "BIGINT", "day": "DATE"}


def sales_df():
    return pd.DataFrame(
        {
            "region": ["N", "S", "N", None],
            "revenue": [1.5, np.nan, np.inf, 4.0],
            "qty": pd.array([1, 2, None, 4], dtype="Int64"),
            "day": pd.to_datetime(["2024-01-01", "2024-01-02", None, "2024-01-04"]),
        }
    )


def test_upload_db_path(tmp_path):
    assert upload_db_path(tmp_path, 7) == tmp_path / "upload_7.duckdb"


def test_write_then_read_back_with_types_and_nulls(tmp_path):
    path = upload_db_path(tmp_path, 1)
    counts = write_upload_file(path, [("sales", sales_df(), SALES_TYPES)])
    assert counts == {"sales": 4} and path.exists()
    assert not path.with_name(path.name + ".tmp").exists()
    con = duckdb.connect(str(path), read_only=True)
    try:
        cols = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'sales'").fetchall())
        assert cols == {"region": "VARCHAR", "revenue": "DOUBLE", "qty": "BIGINT", "day": "DATE"}
        # NaN and inf become NULL, NA stays NULL
        assert con.execute("SELECT COUNT(*) FROM main.sales WHERE revenue IS NULL").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM main.sales WHERE qty IS NULL").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM main.sales WHERE day IS NULL").fetchone()[0] == 1
    finally:
        con.close()


def test_multiple_tables_share_one_file(tmp_path):
    path = upload_db_path(tmp_path, 2)
    a = pd.DataFrame({"x": [1, 2]})
    b = pd.DataFrame({"y": ["a"]})
    counts = write_upload_file(path, [("sheet_a", a, {"x": "BIGINT"}), ("sheet_b", b, {"y": "VARCHAR"})])
    assert counts == {"sheet_a": 2, "sheet_b": 1}
    assert preview_table(path, "sheet_a")[1] == [(1,), (2,)]
    assert preview_table(path, "sheet_b")[0] == ["y"]


def test_reserved_words_and_mixed_case_names_are_quoted(tmp_path):
    path = upload_db_path(tmp_path, 3)
    df = pd.DataFrame({"select": [1], "Order By": [2], 'we"ird': [3]})
    types = {c: "BIGINT" for c in df.columns}
    write_upload_file(path, [("table", df, types)])
    cols, rows = preview_table(path, "table")
    assert cols == ["select", "Order By", 'we"ird'] and rows == [(1, 2, 3)]


def test_empty_frame_creates_empty_table(tmp_path):
    path = upload_db_path(tmp_path, 4)
    df = pd.DataFrame({"x": pd.array([], dtype="Int64")})
    assert write_upload_file(path, [("empty", df, {"x": "BIGINT"})]) == {"empty": 0}
    assert preview_table(path, "empty")[1] == []


def test_failed_write_leaves_no_files(tmp_path):
    path = upload_db_path(tmp_path, 5)
    good = pd.DataFrame({"x": [1]})
    with pytest.raises(Exception):
        write_upload_file(path, [("ok", good, {"x": "BIGINT"}), ("bad", good, {"x": "NOT_A_TYPE"})])
    assert list(tmp_path.iterdir()) == []


def test_run_select_joins_across_two_upload_files(tmp_path):
    p1, p2 = upload_db_path(tmp_path, 1), upload_db_path(tmp_path, 2)
    write_upload_file(p1, [("a", pd.DataFrame({"k": [1, 2], "v": [10, 20]}), {"k": "BIGINT", "v": "BIGINT"})])
    write_upload_file(p2, [("b", pd.DataFrame({"k": [2, 3], "w": [200, 300]}), {"k": "BIGINT", "w": "BIGINT"})])
    out = run_select(
        {1: p1, 2: p2}, {"a": 1, "b": 2},
        "SELECT a.k, a.v, b.w FROM data.a a JOIN data.b b ON a.k = b.k", max_rows=100, timeout_s=10,
    )
    assert out["columns"] == ["k", "v", "w"] and out["rows"] == [[2, 20, 200]] and out["truncated"] is False


def test_run_select_caps_rows(tmp_path):
    p = upload_db_path(tmp_path, 1)
    write_upload_file(p, [("n", pd.DataFrame({"x": list(range(50))}), {"x": "BIGINT"})])
    out = run_select({1: p}, {"n": 1}, "SELECT x FROM data.n ORDER BY x", max_rows=10, timeout_s=10)
    assert len(out["rows"]) == 10 and out["truncated"] is True


def test_run_select_with_no_tables(tmp_path):
    assert run_select({}, {}, "SELECT 1 AS one", max_rows=5, timeout_s=5)["rows"] == [[1]]


def test_concurrent_read_only_attach_on_one_file(tmp_path):
    """Spike: the query path depends on many connections attaching one file read-only at once."""
    p = upload_db_path(tmp_path, 1)
    write_upload_file(p, [("n", pd.DataFrame({"x": list(range(1000))}), {"x": "BIGINT"})])
    results, errors = [], []

    def worker():
        try:
            out = run_select({1: p}, {"n": 1}, "SELECT SUM(x) FROM data.n", max_rows=10, timeout_s=20)
            results.append(out["rows"][0][0])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and results == [sum(range(1000))] * 8


def test_connection_is_locked_down(tmp_path):
    p = upload_db_path(tmp_path, 1)
    write_upload_file(p, [("n", pd.DataFrame({"x": [1]}), {"x": "BIGINT"})])
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    for sql in (
        f"SELECT * FROM read_csv('{secret.as_posix()}')",
        "SET enable_external_access = true",
        "INSTALL httpfs",
        f"COPY (SELECT 1) TO '{(tmp_path / 'out.csv').as_posix()}'",
        f"ATTACH '{(tmp_path / 'other.duckdb').as_posix()}' AS other",
    ):
        with pytest.raises(DuckDBError):
            run_select({1: p}, {"n": 1}, sql, max_rows=5, timeout_s=10)
    assert not (tmp_path / "out.csv").exists() and not (tmp_path / "other.duckdb").exists()


def test_attached_files_are_read_only(tmp_path):
    p = upload_db_path(tmp_path, 1)
    write_upload_file(p, [("n", pd.DataFrame({"x": [1]}), {"x": "BIGINT"})])
    with pytest.raises(DuckDBError):
        run_select({1: p}, {"n": 1}, "DELETE FROM up_1.main.n", max_rows=5, timeout_s=10)
    assert preview_table(p, "n")[1] == [(1,)]


def test_timeout_interrupts_runaway_query(tmp_path):
    started = time.perf_counter()
    with pytest.raises(DuckDBTimeout):
        run_select({}, {}, "SELECT COUNT(*) FROM range(1000000000) a, range(1000000000) b", max_rows=5, timeout_s=0.5)
    assert time.perf_counter() - started < 10


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(DuckDBMissingFile):
        run_select({1: tmp_path / "gone.duckdb"}, {"n": 1}, "SELECT * FROM data.n", max_rows=5, timeout_s=5)
    with pytest.raises(DuckDBMissingFile):
        preview_table(tmp_path / "gone.duckdb", "n")


def test_unknown_table_in_mapping_is_an_error(tmp_path):
    p = upload_db_path(tmp_path, 1)
    write_upload_file(p, [("n", pd.DataFrame({"x": [1]}), {"x": "BIGINT"})])
    with pytest.raises(DuckDBError):
        run_select({1: p}, {"missing": 1}, "SELECT * FROM data.missing", max_rows=5, timeout_s=5)


def test_remove_upload_file_and_cleanup_stale_tmp(tmp_path):
    p = upload_db_path(tmp_path, 1)
    write_upload_file(p, [("n", pd.DataFrame({"x": [1]}), {"x": "BIGINT"})])
    remove_upload_file(p)
    assert not p.exists()
    remove_upload_file(p)  # idempotent
    (tmp_path / "upload_9.duckdb.tmp").write_text("junk")
    (tmp_path / "upload_9.duckdb.tmp.wal").write_text("junk")
    (tmp_path / "upload_2.duckdb").write_text("keep")
    assert cleanup_stale_tmp(tmp_path) == 2
    assert [f.name for f in tmp_path.iterdir()] == ["upload_2.duckdb"]
    assert cleanup_stale_tmp(tmp_path / "does_not_exist") == 0
```

- [ ] **Step 4: Run to verify they fail**

Run: `.\run-tests.bat tests\unit\test_duckdb_store.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'app.duckdb_store'`.

- [ ] **Step 5: Implement `backend/app/duckdb_store.py`**

```python
"""DuckDB storage and query helpers for uploaded structured data.

One file per upload (``upload_<id>.duckdb``) holds every table produced by that upload under schema ``main``.
Everything here is synchronous and meant to run in a worker thread (``asyncio.to_thread``).
"""
import logging
import os
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
            for upload_id, p in files.items():
                con.execute(f"ATTACH {_lit(Path(p))} AS up_{int(upload_id)} (READ_ONLY)")
            con.execute("CREATE SCHEMA data")
            for slug, upload_id in tables.items():
                con.execute(f"CREATE VIEW data.{_ident(slug)} AS SELECT * FROM up_{int(upload_id)}.main.{_ident(slug)}")
            con.execute(f"SET memory_limit = {_lit(memory_limit)}")
            con.execute(f"SET threads = {int(threads)}")
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
        except duckdb.Error as exc:
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
```

- [ ] **Step 6: Run to verify they pass**

Run: `.\run-tests.bat tests\unit\test_duckdb_store.py -q`
Expected: all pass. Notes for the implementer: (a) if `test_timeout_interrupts_runaway_query` is flaky, lengthen the cross join (do not loosen the 10 s bound); (b) if DuckDB reports a different error type for any "locked down" statement than `duckdb.Error`, fix `run_select`'s `except` (all must surface as `DuckDBError`); (c) if the concurrency spike fails, STOP and report BLOCKED with the error text.

- [ ] **Step 7: Commit**

```bash
git add ai-analytics-dashboard/backend/requirements.txt ai-analytics-dashboard/backend/app/config.py ai-analytics-dashboard/backend/app/duckdb_store.py ai-analytics-dashboard/backend/tests/unit/test_duckdb_store.py ai-analytics-dashboard/run-tests.bat ai-analytics-dashboard/run-backend.bat
git commit -m "feat(duckdb): per-upload DuckDB store with locked-down read-only query runner"
```

---

### Task 2: DuckDB SQL validator, agent prompt and demo SQL

**Files:**
- Modify: `backend/app/guardrails/sql_validator.py` (rewrite as below)
- Modify: `backend/app/agents/sql_agent.py` (`SYSTEM` prompt)
- Modify: `backend/app/llm/demo_script.py:27-29` (three SQL constants)
- Test: `backend/tests/unit/test_sql_validator.py` (rewrite), `backend/tests/unit/test_review_fixes.py` (update), `backend/tests/unit/test_sql_agent_prompt.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `validate_sql(sql, schema, max_rows) -> str` (unchanged signature; output SQL is DuckDB dialect, tables qualified `data.<slug>`), `SQLValidationError`. `schema` is `{slug: {column: duckdb_type}}`.

- [ ] **Step 1: Rewrite `backend/tests/unit/test_sql_validator.py`**

```python
import pytest

from app.guardrails.sql_validator import SQLValidationError, validate_sql

SCHEMA = {
    "sales": {"order_id": "BIGINT", "order_date": "DATE", "region": "VARCHAR", "category": "VARCHAR", "revenue": "DOUBLE"},
    "employees": {"employee_id": "BIGINT", "department": "VARCHAR", "salary": "BIGINT"},
}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, SUM(revenue) AS total FROM sales GROUP BY region ORDER BY total DESC",
        "SELECT * FROM data.sales",
        "WITH m AS (SELECT date_trunc('month', order_date) AS month, SUM(revenue) AS rev FROM sales GROUP BY 1) "
        "SELECT month, rev FROM m ORDER BY month",
        "SELECT s.region, e.department FROM sales s JOIN employees e ON s.order_id = e.employee_id",
        "SELECT region FROM sales UNION SELECT department FROM employees",
        "SELECT COUNT(*) FROM sales WHERE region = 'East'",
        "SELECT EXTRACT(MONTH FROM order_date) AS m, AVG(revenue) FROM sales GROUP BY m",
        "SELECT region, ROUND(SUM(revenue), 2) AS total FROM sales GROUP BY region",
        "SELECT region, ROUND(SUM(revenue)::DOUBLE, 2) AS total FROM sales GROUP BY region",
        "SELECT department, AVG(salary) FILTER (WHERE salary > 0) FROM employees GROUP BY department",
        "SELECT region, revenue, RANK() OVER (PARTITION BY region ORDER BY revenue DESC) AS r FROM sales",
        "SELECT region, strftime(order_date, '%Y-%m') AS ym FROM sales WHERE order_date >= CURRENT_DATE - INTERVAL 30 DAY",
    ],
)
def test_allowed(sql):
    out = validate_sql(sql, SCHEMA, 5000)
    assert "data." in out
    assert "LIMIT" in out.upper()


@pytest.mark.parametrize(
    "sql, fragment",
    [
        ("DROP TABLE sales", "Only SELECT"),
        ("DELETE FROM sales", "Only SELECT"),
        ("UPDATE sales SET revenue = 0", "Only SELECT"),
        ("INSERT INTO sales (order_id) VALUES (1)", "Only SELECT"),
        ("TRUNCATE sales", "Only SELECT"),
        ("CREATE TABLE x AS SELECT * FROM sales", "Only SELECT"),
        ("COPY sales TO '/tmp/x'", "Only SELECT"),
        ("ATTACH '/tmp/evil.duckdb' AS evil", "Only SELECT"),
        ("INSTALL httpfs", "Only SELECT"),
        ("PRAGMA database_list", "Only SELECT"),
        ("SET enable_external_access = true", "Only SELECT"),
        ("SELECT 1; DROP TABLE sales", "one SQL statement"),
        ("SELECT * FROM sales; SELECT * FROM employees", "one SQL statement"),
        ("SELECT * FROM sales -- note\n; DROP TABLE sales", "one SQL statement"),
        ("SELECT * FROM app.users", "schema 'app'"),
        ("SELECT * FROM information_schema.tables", "schema 'information_schema'"),
        ("SELECT * FROM main.sales", "schema 'main'"),
        ("SELECT * FROM users", "Unknown table 'users'"),
        ("SELECT * FROM duckdb_tables", "Unknown table"),
        ("SELECT * FROM read_csv('/etc/passwd')", "not allowed"),
        ("SELECT * FROM read_text('/etc/passwd')", "not allowed"),
        ("SELECT * FROM '/etc/passwd.csv'", "not allowed"),
        ("SELECT * FROM glob('/*')", "not allowed"),
        ("SELECT * FROM duckdb_settings()", "not allowed"),
        ("SELECT * FROM range(10)", "not allowed"),
        ("SELECT getenv('HOME')", "not allowed"),
        ("SELECT current_setting('threads')", "not allowed"),
        ("SELECT version()", "not allowed"),
        ("SELECT secret_col FROM sales", "could not be resolved"),
        ("SELECT * FROM sales LIMIT (SELECT 1)", "LIMIT"),
        ("", "one SQL statement"),
        ("this is not sql at all", ""),
    ],
)
def test_blocked(sql, fragment):
    with pytest.raises(SQLValidationError) as err:
        validate_sql(sql, SCHEMA, 5000)
    assert fragment in str(err.value)


def test_limit_added_kept_and_clamped():
    assert validate_sql("SELECT region FROM sales", SCHEMA, 5000).endswith("LIMIT 5000")
    assert validate_sql("SELECT region FROM sales LIMIT 10", SCHEMA, 5000).endswith("LIMIT 10")
    assert validate_sql("SELECT region FROM sales LIMIT 99999", SCHEMA, 5000).endswith("LIMIT 5000")


def test_tables_are_schema_qualified():
    out = validate_sql("SELECT region FROM sales", SCHEMA, 100)
    assert out == "SELECT region FROM data.sales LIMIT 100"
```

- [ ] **Step 2: Update `backend/tests/unit/test_review_fixes.py`** — set `SCHEMA = {"sales": {"region": "VARCHAR", "revenue": "DOUBLE"}}`; keep the CTE-shadowing and legitimate-CTE tests (they remain valid; the `pg_*`/`information_schema` CTE-name guard stays). Replace the `test_quoted_and_dotted_function_names_are_blocked` parameter list with:

```python
        'SELECT "getenv"(\'HOME\')',
        'SELECT "current_setting"(\'threads\')',
        'SELECT "read_text"(\'/etc/passwd\')',
        'SELECT "glob"(\'/*\')',
        "SELECT main.getenv('HOME')",
```

If a listed case does not parse in sqlglot's DuckDB dialect, replace it with the closest equivalent that parses; every case must raise `SQLValidationError` matching `"not allowed"`.

- [ ] **Step 3: Create `backend/tests/unit/test_sql_agent_prompt.py`**

```python
from app.agents.sql_agent import SYSTEM


def test_prompt_targets_duckdb_not_postgres():
    assert "DuckDB" in SYSTEM and "PostgreSQL" not in SYSTEM
    assert "data.<table>" in SYSTEM
    assert "::numeric" not in SYSTEM
```

- [ ] **Step 4: Run to verify failures**

Run: `.\run-tests.bat tests\unit\test_sql_validator.py tests\unit\test_review_fixes.py tests\unit\test_sql_agent_prompt.py -q`
Expected: many FAIL (Postgres-dialect parsing, missing table-function rules, prompt text).

- [ ] **Step 5: Rewrite `backend/app/guardrails/sql_validator.py`**

```python
"""Validate LLM-generated SQL before it can touch DuckDB.

Defense in depth: this parser-level check is backed by a locked-down DuckDB connection
(read-only attached files, no external access, locked configuration, memory and time limits).
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

DIALECT = "duckdb"
ALLOWED_SCHEMA = "data"
DENY_FUNC_PREFIXES = ("read_", "write_", "pragma_", "duckdb_", "sniff_", "parquet_", "pg_")
DENY_FUNCS = {
    "glob", "getenv", "query", "query_table", "load_extension", "install_extension", "which_secret",
    "list_secrets", "current_setting", "set_config", "version", "current_user", "session_user",
    "current_database", "current_schema", "current_schemas", "current_catalog", "range", "generate_series",
}
_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command", "Merge",
    "TruncateTable", "Into", "Lock", "Set", "Transaction", "Commit", "Rollback", "Grant", "Copy",
    "Use", "Pragma", "Cache", "Uncache", "Refresh", "Analyze", "Attach", "Detach", "Install", "Export",
)
FORBIDDEN_NODES = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NAMES) if isinstance(t, type))
_SET_OP_NAMES = ("SetOperation", "Union", "Except", "Intersect")
SET_OPS = tuple(t for t in (getattr(exp, n, None) for n in _SET_OP_NAMES) if isinstance(t, type))


class SQLValidationError(ValueError):
    pass


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        # `.name` is the unquoted text, so "getenv"('X') is caught like getenv('X')
        return func.name.strip('"').lower()
    return func.sql_name().lower()


def _cte_references(tree: exp.Expression) -> set[int]:
    """ids of Table nodes that refer to a CTE visible in their *own* scope.

    A CTE name only shadows tables inside the scope that defines it; a name match
    elsewhere must still be treated as a real table.
    """
    try:
        scopes = traverse_scope(tree)
    except SqlglotError as exc:
        raise SQLValidationError(f"SQL could not be analyzed: {exc}") from exc
    refs: set[int] = set()
    for scope in scopes:
        visible = {name.lower() for name in scope.cte_sources}
        for table in scope.tables:
            if not table.db and (table.name or "").lower() in visible:
                refs.add(id(table))
    return refs


def _check_function(name: str) -> None:
    if name.startswith(DENY_FUNC_PREFIXES) or name in DENY_FUNCS:
        raise SQLValidationError(f"Function '{name}' is not allowed.")


def validate_sql(sql: str, schema: dict[str, dict[str, str]], max_rows: int) -> str:
    try:
        statements = [s for s in sqlglot.parse(sql or "", read=DIALECT) if s is not None]
    except (ParseError, SqlglotError) as exc:
        raise SQLValidationError(f"SQL could not be parsed: {exc}") from exc
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, *SET_OPS)):
        raise SQLValidationError("Only SELECT queries are allowed.")

    for node in tree.find_all(exp.Expression):
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLValidationError(f"Forbidden SQL construct: {type(node).__name__}.")

    for func in tree.find_all(exp.Func):
        _check_function(_function_name(func))

    for cte in tree.find_all(exp.CTE):
        cte_name = cte.alias_or_name.lower()
        if cte_name.startswith(("pg_", "duckdb_")) or cte_name == "information_schema":
            raise SQLValidationError(f"CTE name '{cte_name}' is not allowed (it shadows a system relation).")
    cte_refs = _cte_references(tree)
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            # read_csv(...), glob(...), range(...), FROM 'file.csv' (replacement scans) and similar
            raise SQLValidationError("Table functions and file paths are not allowed; use the listed tables only.")
        name = (table.name or "").lower()
        db = (table.db or "").lower()
        if table.args.get("catalog"):
            raise SQLValidationError("Cross-database references are not allowed.")
        if id(table) in cte_refs:
            continue
        if db and db != ALLOWED_SCHEMA:
            raise SQLValidationError(f"Access to schema '{db}' is not allowed. Use tables in schema 'data' only.")
        if name not in schema:
            available = ", ".join(sorted(schema)) or "(none)"
            raise SQLValidationError(f"Unknown table '{name or table.sql()}'. Available tables: {available}.")
        if not db:
            table.set("db", exp.to_identifier(ALLOWED_SCHEMA))

    try:
        qualify(
            tree.copy(),
            db=ALLOWED_SCHEMA,
            schema={ALLOWED_SCHEMA: {t: dict(cols) for t, cols in schema.items()}},
            dialect=DIALECT,
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
    except SqlglotError as exc:
        raise SQLValidationError(f"Column validation failed: {exc}") from exc

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)
    else:
        value = limit.args.get("expression")
        if not isinstance(value, exp.Literal) or value.is_string:
            raise SQLValidationError("LIMIT must be a plain number.")
        if int(value.this) > max_rows:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return tree.sql(dialect=DIALECT)
```

- [ ] **Step 6: Update the agent prompt and demo SQL**

In `backend/app/agents/sql_agent.py` replace `SYSTEM` with:

```python
SYSTEM = """You write ONE DuckDB SQL SELECT query that answers an analytics task.
Rules:
- Use ONLY the tables and columns listed in the schema. Never invent tables or columns.
- Reference tables as data.<table>.
- Read-only: a single SELECT (CTEs allowed). No INSERT/UPDATE/DELETE/DDL, no file or system functions (read_csv, glob, getenv, ...).
- Give computed columns short snake_case aliases.
- Prefer compact aggregated results over raw rows; order results meaningfully.
- For dates use date_trunc(...) or EXTRACT(...) on date/timestamp columns; cast date_trunc results with ::date for dates.
- Round money/averages with ROUND(x, 2); cast integer sums/averages with ::DOUBLE first when needed.
If the task cannot be answered with this schema, return sql as an empty string and explain why."""
```

In `backend/app/llm/demo_script.py` set:

```python
SQL_REGION = "SELECT region, ROUND(SUM(revenue), 2) AS total_revenue FROM data.sales_2024 GROUP BY region ORDER BY total_revenue DESC"
SQL_MONTHLY = "SELECT date_trunc('month', order_date)::date AS month, ROUND(SUM(revenue), 2) AS revenue FROM data.sales_2024 GROUP BY 1 ORDER BY 1"
SQL_SALARY = "SELECT department, ROUND(AVG(salary), 2) AS avg_salary, COUNT(*) AS headcount FROM data.employees GROUP BY department ORDER BY avg_salary DESC"
```

(Keep whatever the surrounding lines are; only the three string values change.)

- [ ] **Step 7: Run to verify they pass**

Run: `.\run-tests.bat tests\unit\test_sql_validator.py tests\unit\test_review_fixes.py tests\unit\test_sql_agent_prompt.py -q`
Expected: all pass. For any `test_blocked` row whose message fragment differs because sqlglot's DuckDB parser rejects the statement earlier (e.g. `SET`/`PRAGMA` parse to `Command`), the statement must still be rejected: change only the expected fragment to the observed one **if the rejection is genuine** (never make a bad statement pass). Record each fragment change in your report.

- [ ] **Step 8: Commit**

```bash
git add ai-analytics-dashboard/backend/app/guardrails/sql_validator.py ai-analytics-dashboard/backend/app/agents/sql_agent.py ai-analytics-dashboard/backend/app/llm/demo_script.py ai-analytics-dashboard/backend/tests/unit/test_sql_validator.py ai-analytics-dashboard/backend/tests/unit/test_review_fixes.py ai-analytics-dashboard/backend/tests/unit/test_sql_agent_prompt.py
git commit -m "feat(duckdb): DuckDB-dialect SQL validator, prompt and demo SQL"
```

---

### Task 3: Ingestion writes DuckDB files

**Files:**
- Modify: `backend/app/ingestion/tabular.py`, `backend/app/ingestion/pipeline.py`, `backend/app/catalog.py`, `backend/app/main.py` (startup only), `backend/tests/conftest.py`, `backend/tests/integration/conftest.py`, `backend/tests/integration/test_review_fixes.py`, `backend/tests/unit/test_tabular.py`
- Create: `backend/tests/integration/test_duckdb_ingestion.py`

**Interfaces:**
- Consumes: `write_upload_file`, `upload_db_path`, `remove_upload_file`, `cleanup_stale_tmp` (Task 1), `settings.duckdb_path`.
- Produces (used by Tasks 4, 5): `DatasetInfo.upload_id: int` (= `parent_upload_id or id`); after a successful upload `<duckdb_path>/upload_<U>.duckdb` exists with `main."<slug>"` tables; `Dataset.row_count` set; `DatasetColumn.pg_type` holds DuckDB type names; `unique_slug(session, base, extra_taken: set[str] | None = None)`.

- [ ] **Step 1: Update `backend/tests/unit/test_tabular.py` expectations** (failing first). Replace the type strings: `"bigint"`→`"BIGINT"`, `"double precision"`→`"DOUBLE"`, `"date"`→`"DATE"`, `"timestamp"`→`"TIMESTAMP"`, `"boolean"`→`"BOOLEAN"`, `"text"`→`"VARCHAR"` in every assertion. Run `.\run-tests.bat tests\unit\test_tabular.py -q` — expected FAIL.

- [ ] **Step 2: Update `tabular.py`** — in `infer_types` change every type label to the DuckDB names above, including `_date_or_timestamp` (returns `"DATE"`/`"TIMESTAMP"`). Delete `_convert`, `to_records`, `create_and_load_table` and the now-unused imports (`asyncio`, `text`, `AsyncConnection`). Keep `math`, `re`, `pandas`.

Run `.\run-tests.bat tests\unit\test_tabular.py tests\unit\test_intent.py -q` — expected pass.

- [ ] **Step 3: Test infrastructure** — in `backend/tests/conftest.py` add `import tempfile` and, next to the `UPLOAD_DIR` line:

```python
os.environ["DUCKDB_DIR"] = os.path.join(tempfile.gettempdir(), "test_duckdb")
```

In `backend/tests/integration/conftest.py` add `import shutil` and, at the end of `reset_database` (after the Redis flush):

```python
    shutil.rmtree(get_settings().duckdb_path, ignore_errors=True)
    get_settings().duckdb_path.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Write the failing integration tests** — `backend/tests/integration/test_duckdb_ingestion.py`

```python
import asyncio

import duckdb
from sqlalchemy import text

import app.ingestion.pipeline as pipeline
from app.duckdb_store import upload_db_path
from helpers import auth, upload_and_wait


def upload_id(d: dict) -> int:
    return d["parent_upload_id"] or d["id"]


def db_file(client, d: dict):
    return upload_db_path(client.app.state.settings.duckdb_path, upload_id(d))


async def wait_for_terminal(client, token, attempts: int = 100) -> dict:
    for _ in range(attempts):
        d = (await client.get("/api/datasets", headers=auth(token))).json()[0]
        if d["status"] in ("ready", "failed"):
            return d
        await asyncio.sleep(0.2)
    raise AssertionError(f"Dataset never finished: {d}")


def duck_rows(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


async def test_csv_becomes_a_duckdb_file_not_a_postgres_table(client, admin_token, fixtures_dir):
    ds = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    d = ds[0]
    assert d["status"] == "ready" and d["slug"] == "sales_2024" and d["row_count"] == 2000
    path = db_file(client, d)
    assert path.exists()
    assert duck_rows(path, 'SELECT COUNT(*) FROM main."sales_2024"') == [(2000,)]
    async with client.app.state.sessionmaker() as s:
        assert (await s.scalar(text("SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'data'"))) == 0


async def test_column_types_are_duckdb_names(client, admin_token, fixtures_dir):
    d = (await upload_and_wait(client, admin_token, [fixtures_dir / "employees.json"]))[0]
    detail = (await client.get(f"/api/datasets/{d['id']}", headers=auth(admin_token))).json()
    types = {c["column_name"]: c["pg_type"] for c in detail["columns"]}
    assert types["salary"] == "BIGINT" and types["hire_date"] == "DATE" and types["address_city"] == "VARCHAR"


async def test_pdf_tables_go_to_the_same_upload_file_as_their_siblings(client, admin_token, fixtures_dir):
    ds = await upload_and_wait(client, admin_token, [fixtures_dir / "annual_report_2024.pdf"])
    table = next(d for d in ds if d["kind"] == "table")
    assert table["row_count"] == 4
    assert duck_rows(db_file(client, table), f'SELECT COUNT(*) FROM main."{table["slug"]}"') == [(4,)]


async def test_same_filename_twice_gets_separate_files_and_slugs(client, admin_token, fixtures_dir):
    first = (await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"]))[0]
    second = (await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"]))[0]
    assert first["slug"] == "sales_2024" and second["slug"] == "sales_2024_2"
    assert db_file(client, first) != db_file(client, second) and db_file(client, second).exists()
    assert duck_rows(db_file(client, second), 'SELECT COUNT(*) FROM main."sales_2024_2"') == [(2000,)]


async def test_write_failure_leaves_no_file_and_marks_dataset_failed(client, admin_token, fixtures_dir, monkeypatch):
    def boom(path, tables):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "write_upload_file", boom)
    files = [("files", ("sales.csv", (fixtures_dir / "sales_2024.csv").read_bytes(), "text/csv"))]
    r = await client.post("/api/datasets/upload", files=files, headers=auth(admin_token))
    assert r.status_code == 202
    d = await wait_for_terminal(client, admin_token)
    assert d["status"] == "failed" and "disk full" in d["error"]
    assert list(client.app.state.settings.duckdb_path.iterdir()) == []


async def test_registry_failure_after_write_removes_the_file(client, admin_token, fixtures_dir, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("registry down")

    monkeypatch.setattr(pipeline, "_register_table", boom)
    files = [("files", ("sales.csv", (fixtures_dir / "sales_2024.csv").read_bytes(), "text/csv"))]
    await client.post("/api/datasets/upload", files=files, headers=auth(admin_token))
    d = await wait_for_terminal(client, admin_token)
    assert d["status"] == "failed"
    assert list(client.app.state.settings.duckdb_path.iterdir()) == []
```

- [ ] **Step 5: Run to verify they fail**

Run: `.\run-tests.bat tests\integration\test_duckdb_ingestion.py -q`
Expected: FAIL (no files written; pipeline still loads Postgres).

- [ ] **Step 6: Implement the pipeline change** in `backend/app/ingestion/pipeline.py`:

1. Imports: remove `create_and_load_table` from the `tabular` import; add `from app.duckdb_store import remove_upload_file, upload_db_path, write_upload_file`; add `func` to the `sqlalchemy` import (`from sqlalchemy import func, select, text, update`).
2. `unique_slug` becomes:

```python
async def unique_slug(session, base: str, extra_taken: set[str] | None = None) -> str:
    base = to_identifier(base, "dataset")[:50]
    taken = set((await session.scalars(select(Dataset.slug).where(Dataset.slug.like(f"{base}%")))).all())
    taken |= extra_taken or set()
    slug, k = base, 2
    while slug in taken:
        slug = f"{base}_{k}"
        k += 1
    return slug
```

(Keep the remainder of the existing function body exactly as it was after the `while` loop, i.e. `return slug`.)

3. Replace `_store_table` with two functions:

```python
async def _store_tables(state, reuse_id: int | None, parent_id: int, original_name: str, tables: list[ParsedTable]) -> None:
    """Write every table of this upload into ONE .duckdb file, then register each as a dataset."""
    prepared = []
    taken: set[str] = set()
    for table in tables:
        # CPU-heavy pandas work runs in a thread so large uploads don't freeze the API
        df, types = await asyncio.to_thread(_prepare_table, table.df)
        descriptions = await _describe_columns(state, table.name_hint, df, types)
        async with state.sessionmaker() as s:
            slug = await unique_slug(s, table.name_hint, taken)
        taken.add(slug)
        prepared.append((table, slug, df, types, descriptions))
    path = upload_db_path(state.settings.duckdb_path, parent_id)
    counts = await asyncio.to_thread(write_upload_file, path, [(slug, df, types) for _, slug, df, types, _ in prepared])
    try:
        for i, (table, slug, df, types, descriptions) in enumerate(prepared):
            await _register_table(
                state, reuse_id if i == 0 else None, parent_id, original_name, table, slug, df, types, descriptions, counts[slug]
            )
    except Exception:
        async with state.sessionmaker() as s:
            ready = await s.scalar(
                select(func.count()).select_from(Dataset).where(
                    Dataset.kind == "table", Dataset.status == "ready", (Dataset.id == parent_id) | (Dataset.parent_upload_id == parent_id)
                )
            )
        if not ready:
            remove_upload_file(path)
        raise


async def _register_table(
    state, reuse_id: int | None, parent_id: int, original_name: str, table: ParsedTable, slug: str,
    df: pd.DataFrame, types: dict[str, str], descriptions: dict[str, str], row_count: int,
) -> None:
    async with state.sessionmaker() as s:
        ds = await _target(s, reuse_id, parent_id)
        is_main = table.name_hint == Path(original_name).stem
        ds.name = original_name if is_main else f"{original_name} ({table.name_hint})"
        ds.slug = slug
        ds.kind = "table"
        ds.row_count = row_count
        await s.flush()
        for col in df.columns:
            s.add(
                DatasetColumn(
                    dataset_id=ds.id,
                    column_name=col,
                    pg_type=types[col],
                    sample_values=_samples(df[col]),
                    description=descriptions.get(col, ""),
                )
            )
        ds.status, ds.error = "ready", None
        await bump_catalog_version(s)  # each registered part becomes visible immediately
        await s.commit()
```

4. In `_store`, replace the table loop with:

```python
async def _store(state, dataset_id: int, original_name: str, result: ParseResult) -> None:
    reuse: int | None = dataset_id
    if result.tables:
        await _store_tables(state, reuse, dataset_id, original_name, result.tables)
        reuse = None
    if result.segments:
        await _store_document(state, reuse, dataset_id, original_name, result.segments)
```

`_prepare_table`, `_describe_columns`, `_samples`, `_target` stay unchanged.

- [ ] **Step 7: Catalog** — in `backend/app/catalog.py` add `upload_id: int = 0` as the **last** field of `DatasetInfo` and construct it in `load_catalog` as
`DatasetInfo(d.id, d.slug, d.name, d.kind, by_dataset.get(d.id, []), d.row_count, d.chunk_count, d.parent_upload_id or d.id)`.

- [ ] **Step 8: Startup** — in `backend/app/main.py` `lifespan`, right after `os.makedirs(settings.upload_dir, exist_ok=True)` add:

```python
    os.makedirs(settings.duckdb_path, exist_ok=True)
    cleanup_stale_tmp(settings.duckdb_path)
```

and import `from app.duckdb_store import cleanup_stale_tmp`. (The `ro_pool` is removed in Task 4.)

- [ ] **Step 9: Fix the existing thread-spy test** — in `backend/tests/integration/test_review_fixes.py` replace `test_dataframe_preparation_runs_off_the_event_loop` with:

```python
async def test_dataframe_preparation_runs_off_the_event_loop(client, admin_token, fixtures_dir, monkeypatch):
    threads: dict[str, str] = {}
    real_infer, real_write = pipeline.infer_types, pipeline.write_upload_file

    def spy_infer(df):
        threads["infer"] = threading.current_thread().name
        return real_infer(df)

    def spy_write(path, tables):
        threads["write"] = threading.current_thread().name
        return real_write(path, tables)

    monkeypatch.setattr(pipeline, "infer_types", spy_infer)
    monkeypatch.setattr(pipeline, "write_upload_file", spy_write)
    datasets = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    assert datasets[0]["status"] == "ready"
    assert threads["infer"] != "MainThread" and threads["write"] != "MainThread"
```

and remove the now-unused `import app.ingestion.tabular as tabular`.

- [ ] **Step 10: Run to verify**

Run: `.\run-tests.bat tests\integration\test_duckdb_ingestion.py tests\unit tests\integration\test_review_fixes.py -q`
Expected: the new ingestion tests, all unit tests and `test_review_fixes.py` pass **except** `test_query_timeout_returns_json_error` (needs the query path from Task 4; expected red until then — note it in your report). Other integration files (query/datasets/agents/dashboard) are expected red until Tasks 4–5.

- [ ] **Step 11: Commit**

```bash
git add ai-analytics-dashboard/backend/app/ingestion/tabular.py ai-analytics-dashboard/backend/app/ingestion/pipeline.py ai-analytics-dashboard/backend/app/catalog.py ai-analytics-dashboard/backend/app/main.py ai-analytics-dashboard/backend/tests/conftest.py ai-analytics-dashboard/backend/tests/integration/conftest.py ai-analytics-dashboard/backend/tests/integration/test_review_fixes.py ai-analytics-dashboard/backend/tests/unit/test_tabular.py ai-analytics-dashboard/backend/tests/integration/test_duckdb_ingestion.py
git commit -m "feat(duckdb): ingest structured uploads into per-upload DuckDB files"
```

---

### Task 4: Query path, preview and delete use DuckDB

**Files:**
- Modify: `backend/app/agents/sql_exec.py` (rewrite), `backend/app/api/datasets.py` (preview + delete), `backend/app/main.py` (remove `ro_pool`)
- Test: `backend/tests/integration/test_duckdb_query.py` (create)

**Interfaces:**
- Consumes: `run_select`, `preview_table`, `remove_upload_file`, `upload_db_path`, `DuckDBError`, `DuckDBMissingFile` (Task 1); `DatasetInfo.upload_id` (Task 3); `validate_sql` (Task 2).
- Produces: `run_readonly_query(state, sql, version, settings) -> dict` (same shape as before); `referenced_tables(sql) -> list[str]`; dataset preview/delete work from DuckDB. After this task `app.state.ro_pool` no longer exists.

- [ ] **Step 1: Write the failing tests** — `backend/tests/integration/test_duckdb_query.py`

```python
import json

import pytest

import app.api.datasets as datasets_api
from app.agents.sql_exec import QueryExecutionError, referenced_tables, run_readonly_query
from app.catalog import load_catalog
from app.duckdb_store import upload_db_path
from helpers import auth, upload_and_wait


@pytest.fixture
async def loaded(client, admin_token, fixtures_dir):
    ds = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv", fixtures_dir / "employees.json"])
    state = client.app.state
    catalog = await load_catalog(state)
    expected = json.loads((fixtures_dir / "expected_answers.json").read_text())
    return state, catalog, expected, {d["slug"]: d for d in ds}


def test_referenced_tables():
    assert referenced_tables("SELECT * FROM data.a JOIN data.b ON a.k = b.k") == ["a", "b"]
    assert referenced_tables("WITH t AS (SELECT * FROM data.a) SELECT * FROM t") == ["a"]
    assert referenced_tables("SELECT 1") == []


async def test_query_matches_ground_truth_and_is_cached(loaded):
    state, catalog, expected, _ = loaded
    sql = "SELECT region, SUM(revenue) AS total FROM data.sales_2024 GROUP BY region ORDER BY region LIMIT 5000"
    out = await run_readonly_query(state, sql, catalog.version, state.settings)
    got = {r[0]: r[1] for r in out["rows"]}
    for region, total in expected["revenue_by_region"].items():
        assert got[region] == pytest.approx(total, abs=0.01)
    assert out["columns"] == ["region", "total"] and out["cache_hit"] is False
    assert (await run_readonly_query(state, sql, catalog.version, state.settings))["cache_hit"] is True


async def test_query_across_two_uploads(loaded):
    state, catalog, _, _ = loaded
    sql = "SELECT (SELECT COUNT(*) FROM data.sales_2024) AS s, (SELECT COUNT(*) FROM data.employees) AS e LIMIT 5000"
    out = await run_readonly_query(state, sql, catalog.version, state.settings)
    assert out["rows"] == [[2000, 200]]


async def test_unknown_table_is_a_query_error(loaded):
    state, catalog, _, _ = loaded
    with pytest.raises(QueryExecutionError, match="Unknown table"):
        await run_readonly_query(state, "SELECT * FROM data.nope LIMIT 5", catalog.version, state.settings)


async def test_missing_file_gives_a_clear_error(loaded):
    state, catalog, _, by_slug = loaded
    d = by_slug["sales_2024"]
    upload_db_path(state.settings.duckdb_path, d["parent_upload_id"] or d["id"]).unlink()
    with pytest.raises(QueryExecutionError, match="missing"):
        await run_readonly_query(state, "SELECT COUNT(*) FROM data.sales_2024 LIMIT 5", catalog.version, state.settings)


async def test_preview_comes_from_duckdb(client, admin_token, loaded):
    _, _, _, by_slug = loaded
    detail = (await client.get(f"/api/datasets/{by_slug['employees']['id']}", headers=auth(admin_token))).json()
    assert len(detail["preview"]["rows"]) == 20 and "salary" in detail["preview"]["columns"]


async def test_preview_is_empty_when_file_is_gone(client, admin_token, loaded):
    state, _, _, by_slug = loaded
    d = by_slug["employees"]
    upload_db_path(state.settings.duckdb_path, d["parent_upload_id"] or d["id"]).unlink()
    detail = (await client.get(f"/api/datasets/{d['id']}", headers=auth(admin_token))).json()
    assert detail["preview"] == {}


async def test_delete_removes_the_file_when_last_table_goes(client, admin_token, loaded):
    state, _, _, by_slug = loaded
    d = by_slug["sales_2024"]
    path = upload_db_path(state.settings.duckdb_path, d["parent_upload_id"] or d["id"])
    assert path.exists()
    assert (await client.delete(f"/api/datasets/{d['id']}", headers=auth(admin_token))).status_code == 204
    assert not path.exists()
    assert "sales_2024" not in [x["slug"] for x in (await client.get("/api/datasets", headers=auth(admin_token))).json()]


async def test_delete_is_refused_with_409_when_the_file_is_in_use(client, admin_token, loaded, monkeypatch):
    state, _, _, by_slug = loaded
    d = by_slug["sales_2024"]

    def locked(path):
        raise PermissionError("in use")

    monkeypatch.setattr(datasets_api, "remove_upload_file", locked)
    r = await client.delete(f"/api/datasets/{d['id']}", headers=auth(admin_token))
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    assert d["slug"] in [x["slug"] for x in (await client.get("/api/datasets", headers=auth(admin_token))).json()]


async def test_app_no_longer_keeps_a_postgres_query_pool(client):
    assert not hasattr(client.app.state, "ro_pool")
```

- [ ] **Step 2: Run to verify failures**

Run: `.\run-tests.bat tests\integration\test_duckdb_query.py -q`
Expected: FAIL (`referenced_tables` missing, Postgres execution).

- [ ] **Step 3: Rewrite `backend/app/agents/sql_exec.py`**

```python
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
```

- [ ] **Step 4: Preview and delete in `backend/app/api/datasets.py`**

Add imports: `import asyncio` (if not present), `from app.duckdb_store import DuckDBError, preview_table, remove_upload_file, upload_db_path`. Replace the table preview block in `get_dataset` with:

```python
    if ds.status == "ready" and ds.kind == "table":
        path = upload_db_path(request.app.state.settings.duckdb_path, ds.parent_upload_id or ds.id)
        try:
            cols, rows = await asyncio.to_thread(preview_table, path, ds.slug, 20)
            preview = {"columns": cols, "rows": [[to_jsonable(v) for v in r] for r in rows]}
        except DuckDBError:
            preview = {}  # file missing or unreadable: show the schema without a preview
```

Replace `delete_dataset` with:

```python
@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: int, request: Request, admin: User = Depends(admin_user), session: AsyncSession = Depends(get_session)
):
    ds = await session.get(Dataset, dataset_id)
    if ds is None:
        raise api_error(404, "not_found", "Dataset not found.")
    if ds.status in ("pending", "processing"):
        raise api_error(409, "busy", "This dataset is still being processed. Try again shortly.")
    if ds.kind == "table":
        upload_id = ds.parent_upload_id or ds.id
        remaining = await session.scalar(
            select(func.count()).select_from(Dataset).where(
                Dataset.kind == "table", Dataset.id != ds.id, (Dataset.id == upload_id) | (Dataset.parent_upload_id == upload_id)
            )
        )
        if not remaining:
            try:
                remove_upload_file(upload_db_path(request.app.state.settings.duckdb_path, upload_id))
            except PermissionError:
                raise api_error(409, "busy", "The data file is in use by a running query. Try again shortly.")
    stored = ds.stored_path
    await session.delete(ds)
    await bump_catalog_version(session)
    await session.commit()
    if stored:
        remaining_refs = await session.scalar(select(func.count()).select_from(Dataset).where(Dataset.stored_path == stored))
        if not remaining_refs:
            Path(stored).unlink(missing_ok=True)
    return Response(status_code=204)
```

Remove the now-unused `text` import only if nothing else in the file uses it (the document preview still does — keep it).

- [ ] **Step 5: Remove the Postgres query pool** — in `backend/app/main.py` delete the line `app.state.ro_pool = await asyncpg.create_pool(settings.query_ro_url, min_size=1, max_size=5)`, the `await app.state.ro_pool.close()` line, and the `import asyncpg` line (verify nothing else in `main.py` uses `asyncpg`).

- [ ] **Step 6: Run to verify**

Run: `.\run-tests.bat tests\integration\test_duckdb_query.py tests\integration\test_duckdb_ingestion.py tests\integration\test_review_fixes.py tests\unit -q`
Expected: all pass (including `test_query_timeout_returns_json_error`). Old tests in `test_datasets_api.py`, `test_sql_rag_agents.py`, `test_query_api.py`, `test_dashboard_admin.py` may still fail on `ro_pool` assertions — Task 5 fixes them.

- [ ] **Step 7: Commit**

```bash
git add ai-analytics-dashboard/backend/app/agents/sql_exec.py ai-analytics-dashboard/backend/app/api/datasets.py ai-analytics-dashboard/backend/app/main.py ai-analytics-dashboard/backend/tests/integration/test_duckdb_query.py
git commit -m "feat(duckdb): run queries, previews and deletes against DuckDB files"
```

---

### Task 5: Adapt the remaining tests; full suite green

**Files:**
- Modify: `backend/tests/helpers.py`, `backend/tests/integration/test_datasets_api.py`, `backend/tests/integration/test_sql_rag_agents.py`, and any other test that fails for a Postgres-`data`-schema / `ro_pool` assumption.

**Interfaces:**
- Consumes: `upload_db_path`, `settings.duckdb_path`, `DatasetInfo.upload_id`.
- Produces: `helpers.duck_count(client, dataset: dict, slug: str | None = None) -> int` counting rows of a table in the dataset's DuckDB file.

- [ ] **Step 1: Run the whole suite to see the real red list**

Run: `.\run-tests.bat tests -q -x --no-header -p no:cacheprovider` is **not** wanted (it stops at the first failure); run `.\run-tests.bat tests -q` and list every failing test in your report before changing anything.

- [ ] **Step 2: Add the helper** to `backend/tests/helpers.py` (add `import duckdb` and `from app.duckdb_store import upload_db_path` at the top):

```python
def duck_count(client, dataset: dict, slug: str | None = None) -> int:
    """Row count of a table in the dataset's per-upload DuckDB file."""
    path = upload_db_path(client.app.state.settings.duckdb_path, dataset["parent_upload_id"] or dataset["id"])
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(f'SELECT COUNT(*) FROM main."{slug or dataset["slug"]}"').fetchone()[0]
    finally:
        con.close()
```

- [ ] **Step 3: Adapt `test_datasets_api.py`**
  - import `duck_count` and `upload_db_path` as needed;
  - line `ro_pool … COUNT(*) FROM data.sales_2024` → `assert duck_count(client, by_slug["sales_2024"]) == 2000`;
  - type assertions → `types["salary"] == "BIGINT" and types["hire_date"] == "DATE" and types["address_city"] == "VARCHAR"`;
  - `test_same_filename_twice…`: `assert duck_count(client, second[0]) == 2000`;
  - `test_delete_requires_admin_and_drops_table`: after the 204, replace the `to_regclass` assertion with `assert not upload_db_path(client.app.state.settings.duckdb_path, ds[0]["parent_upload_id"] or ds[0]["id"]).exists()`.

- [ ] **Step 4: Adapt `test_sql_rag_agents.py`** — `test_malicious_sql_blocked_and_data_intact`: replace the `ro_pool` block with
`assert duck_count_for(state, catalog, "sales_2024") == 2000` where, since this test has no `client`, add this local helper at the top of the file:

```python
def duck_count_for(state, catalog, slug):
    import duckdb
    from app.duckdb_store import upload_db_path

    info = catalog.get(slug)
    con = duckdb.connect(str(upload_db_path(state.settings.duckdb_path, info.upload_id)), read_only=True)
    try:
        return con.execute(f'SELECT COUNT(*) FROM main."{slug}"').fetchone()[0]
    finally:
        con.close()
```

- [ ] **Step 5: Fix every other failure** from Step 1 with the minimum change that keeps the test asserting the same behavior (e.g. `test_query_api.py` expectations on SQL text or numeric rounding; `ro_pool` references; `pg_type` strings). Do **not** weaken assertions or delete tests; for each non-trivial change, say why in the report. If a failure reveals a real product bug (not a stale assumption), stop and report it instead of papering over it.

- [ ] **Step 6: Run the full suite**

Run: `.\run-tests.bat tests -q`
Expected: all pass (the pre-existing count was 260 backend tests; new DuckDB/unit tests add to it), 1 deselected (live), no new warnings.

- [ ] **Step 7: Commit**

```bash
git add ai-analytics-dashboard/backend/tests
git commit -m "test: adapt integration tests to DuckDB-backed structured data"
```

(Stage only test files you changed, listed explicitly.)

---

### Task 6: Purge script for old Postgres structured data

**Files:**
- Create: `backend/app/maintenance.py`, `backend/scripts/purge_postgres_data.py`
- Test: `backend/tests/integration/test_purge.py`

**Interfaces:**
- Consumes: `create_engine_and_sessionmaker` (`app.db.session`), `get_settings`.
- Produces: `async purge_structured_data(engine, apply: bool) -> dict` returning `{"applied": bool, "dropped_tables": list[str], "deleted_dataset_rows": int}`.

- [ ] **Step 1: Write the failing test** — `backend/tests/integration/test_purge.py`

```python
from sqlalchemy import select, text

from app.db.models import Dataset, DatasetColumn, User
from app.db.versions import get_catalog_version
from app.maintenance import purge_structured_data


async def seed(db):
    async with db.engine.begin() as conn:
        await conn.execute(text('CREATE TABLE data."old_sales" (id int)'))
        await conn.execute(text('CREATE TABLE data."old_hr" (id int)'))
    async with db.sessionmaker() as s:
        s.add(User(email="keep@example.com", password_hash="x", role="user", is_verified=True))
        table = Dataset(name="old.csv", slug="old_sales", kind="table", source_filename="old.csv", file_type="csv", status="ready")
        doc = Dataset(name="notes.txt", slug="notes", kind="document", source_filename="notes.txt", file_type="txt", status="ready")
        s.add_all([table, doc])
        await s.flush()
        s.add(DatasetColumn(dataset_id=table.id, column_name="id", pg_type="bigint"))
        await s.commit()


async def data_tables(db):
    async with db.engine.connect() as c:
        return set((await c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'data'"))).scalars())


async def test_dry_run_changes_nothing(db):
    await seed(db)
    out = await purge_structured_data(db.engine, apply=False)
    assert out == {"applied": False, "dropped_tables": ["old_hr", "old_sales"], "deleted_dataset_rows": 1}
    assert await data_tables(db) == {"old_hr", "old_sales"}
    async with db.sessionmaker() as s:
        assert len((await s.scalars(select(Dataset))).all()) == 2


async def test_apply_drops_tables_and_table_rows_only(db):
    await seed(db)
    async with db.sessionmaker() as s:
        before = await get_catalog_version(s)
    out = await purge_structured_data(db.engine, apply=True)
    assert out["applied"] is True and out["deleted_dataset_rows"] == 1
    assert await data_tables(db) == set()
    async with db.sessionmaker() as s:
        assert [d.slug for d in (await s.scalars(select(Dataset))).all()] == ["notes"]
        assert (await s.scalars(select(DatasetColumn))).all() == []
        assert (await s.scalar(select(User).where(User.email == "keep@example.com"))) is not None
        assert await get_catalog_version(s) == before + 1


async def test_apply_is_idempotent(db):
    await seed(db)
    await purge_structured_data(db.engine, apply=True)
    out = await purge_structured_data(db.engine, apply=True)
    assert out["dropped_tables"] == [] and out["deleted_dataset_rows"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\run-tests.bat tests\integration\test_purge.py -q`
Expected: collection error `No module named 'app.maintenance'`.

- [ ] **Step 3: Implement `backend/app/maintenance.py`**

```python
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
```

- [ ] **Step 4: Implement `backend/scripts/purge_postgres_data.py`**

```python
"""One-off clean-up of structured data left in Postgres by the pre-DuckDB version.

Usage (from the backend folder):
    python scripts/purge_postgres_data.py          # dry run: shows what would be removed
    python scripts/purge_postgres_data.py --yes    # actually drop data.* tables and 'table' dataset rows

Users must re-upload their structured files afterwards (they are stored as DuckDB files now).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.session import create_engine_and_sessionmaker  # noqa: E402
from app.maintenance import purge_structured_data  # noqa: E402


async def main(apply: bool) -> None:
    engine, _ = create_engine_and_sessionmaker(get_settings().database_url)
    try:
        out = await purge_structured_data(engine, apply)
    finally:
        await engine.dispose()
    verb = "Dropped" if apply else "Would drop"
    print(f"{verb} {len(out['dropped_tables'])} Postgres table(s): {', '.join(out['dropped_tables']) or '-'}")
    print(f"{'Deleted' if apply else 'Would delete'} {out['deleted_dataset_rows']} dataset row(s) of kind 'table'.")
    if not apply:
        print("Dry run only. Re-run with --yes to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="apply the changes (default is a dry run)")
    asyncio.run(main(parser.parse_args().yes))
```

- [ ] **Step 5: Run to verify**

Run: `.\run-tests.bat tests\integration\test_purge.py -q` — expected pass. Then, **only against the test database**, smoke the script's dry run: from `backend\` with the same environment variables `run-tests.bat` sets, run `.venv\Scripts\python.exe scripts\purge_postgres_data.py` and confirm it prints "Would drop … Dry run only." Do **not** run it with `--yes` against any real database.

- [ ] **Step 6: Commit**

```bash
git add ai-analytics-dashboard/backend/app/maintenance.py ai-analytics-dashboard/backend/scripts/purge_postgres_data.py ai-analytics-dashboard/backend/tests/integration/test_purge.py
git commit -m "feat: manual purge script for structured data left in Postgres"
```

---

### Task 7: Docs, spec amendments, full verification

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-20-duckdb-structured-data-design.md`

- [ ] **Step 1: README** — add a section "Structured data (DuckDB)" under "Using it" saying: CSV/TSV/Excel/JSON tables (and tables found in PDFs) are converted to one `uploads/duckdb/upload_<id>.duckdb` file per upload; questions about them run in DuckDB (read-only, sandboxed); Postgres keeps users, chats, the dataset registry, audit log, pins and document embeddings. Add under the Configuration table: `DUCKDB_DIR` (default `<UPLOAD_DIR>/duckdb`), `DUCKDB_MEMORY_LIMIT` (`512MB`), `DUCKDB_THREADS` (`2`). Add a "Upgrading from the Postgres-tables version" note: run `python scripts/purge_postgres_data.py` (dry run), then with `--yes`, then re-upload your files. Update the Project layout lines for `duckdb_store.py`, `maintenance.py` and `scripts/purge_postgres_data.py`; the Troubleshooting entry "a dataset's file is missing → re-upload".

- [ ] **Step 2: Spec amendment** — in the spec's section 5 replace the `main.py` bullet's mention of dropping `QUERY_RO_URL` with: "`main.py`: remove the asyncpg `ro_pool` creation/close. `QUERY_RO_URL` / the `query_ro` role stay in config and the DB init script (unused by the app; `test_db.py` still verifies the role is locked down)." Also add to "Testing" that the plan added `tests/unit/test_duckdb_store.py` including the concurrency spike.

- [ ] **Step 3: Full verification**

Run: `.\run-tests.bat tests -q` (whole backend suite; expect all green) and, in `frontend\`, `npm test` and `npm run build` (frontend is unaffected apart from displaying the new type names; both must stay green).

- [ ] **Step 4: Manual check (human, not an agent)** — start `run-backend.bat` / `run-frontend.bat`, upload `backend\tests\fixtures\sales_2024.csv`, confirm `backend\uploads\duckdb\upload_<id>.duckdb` appears (or `%UPLOAD_DIR%\duckdb`), ask "What is the total revenue by region?", reopen the dataset page to see the preview, delete the dataset and confirm the file disappears. State in the report that this step was not performed by the agent.

- [ ] **Step 5: Commit**

```bash
git add ai-analytics-dashboard/README.md ai-analytics-dashboard/docs/superpowers/specs/2026-10-20-duckdb-structured-data-design.md
git commit -m "docs: DuckDB structured data storage, upgrade notes and spec amendments"
```
