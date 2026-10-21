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
