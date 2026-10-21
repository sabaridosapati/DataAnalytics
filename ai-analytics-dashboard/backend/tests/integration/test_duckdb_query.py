import asyncio
import json
import threading
import time

import pytest

import app.agents.sql_exec as sql_exec
import app.api.datasets as datasets_api
from app.agents.sql_exec import QueryExecutionError, referenced_tables, run_readonly_query
from app.catalog import load_catalog
from app.db.models import Dataset
from app.db.versions import bump_catalog_version
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
    assert referenced_tables("SELECT * FROM data.Sales s JOIN DATA.Other o ON 1=1") == ["other", "sales"]


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

async def _max_concurrency(state, settings, monkeypatch, tag, n=4):
    active, peak, lock = 0, 0, threading.Lock()

    def slow(files, tables, sql, **kw):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.15)
        with lock:
            active -= 1
        return {"columns": ["x"], "rows": [[1]], "truncated": False}

    monkeypatch.setattr(sql_exec, "run_select", slow)
    catalog = await load_catalog(state)
    sqls = [f"SELECT {i + tag} AS x, COUNT(*) FROM data.sales_2024 LIMIT 5000" for i in range(n)]
    await asyncio.gather(*(run_readonly_query(state, s, catalog.version, settings) for s in sqls))
    return peak


async def test_concurrent_duckdb_queries_are_limited(loaded, monkeypatch):
    state, _, _, _ = loaded
    one = state.settings.model_copy(update={"duckdb_max_concurrent_queries": 1})
    assert await _max_concurrency(state, one, monkeypatch, 100) == 1
    two = state.settings.model_copy(update={"duckdb_max_concurrent_queries": 2})
    peak = await _max_concurrency(state, two, monkeypatch, 200)
    assert peak == 2


async def test_mixed_case_table_name_resolves(loaded):
    state, catalog, _, _ = loaded
    out = await run_readonly_query(state, "SELECT COUNT(*) FROM data.Sales_2024 LIMIT 5", catalog.version, state.settings)
    assert out["rows"] == [[2000]]


async def test_legacy_table_datasets_without_a_file_are_not_in_the_catalog(loaded):
    state, _, _, _ = loaded
    async with state.sessionmaker() as s:
        s.add(Dataset(name="old.csv", slug="old_sales", kind="table", source_filename="old.csv", file_type="csv", status="ready"))
        await bump_catalog_version(s)
        await s.commit()
    catalog = await load_catalog(state)
    slugs = [d.slug for d in catalog.datasets]
    assert "old_sales" not in slugs and "sales_2024" in slugs and "employees" in slugs
