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
