import json

import pandas as pd

from app.agents.sql_exec import run_readonly_query
from app.catalog import load_catalog
from app.duckdb_store import upload_db_path
from helpers import auth, upload_and_wait


def _file_for(client, d):
    return upload_db_path(client.app.state.settings.duckdb_path, d["parent_upload_id"] or d["id"])


async def test_two_sheet_workbook_shares_one_file_and_deletes_per_table(client, admin_token, tmp_path):
    book = tmp_path / "book.xlsx"
    with pd.ExcelWriter(book, engine="openpyxl") as xw:
        pd.DataFrame({"k": [1, 2, 3], "v": [10, 20, 30]}).to_excel(xw, sheet_name="alpha", index=False)
        pd.DataFrame({"k": [2, 3], "w": [200, 300]}).to_excel(xw, sheet_name="beta", index=False)
    ds = await upload_and_wait(client, admin_token, [book])
    tables = [d for d in ds if d["kind"] == "table"]
    assert len(tables) == 2 and all(d["status"] == "ready" for d in tables), ds
    assert len({_file_for(client, d) for d in tables}) == 1
    path = _file_for(client, tables[0])
    assert path.exists()
    a = next(d for d in tables if d["slug"].endswith("alpha"))
    b = next(d for d in tables if d["slug"].endswith("beta"))

    state = client.app.state
    catalog = await load_catalog(state)
    sql = f"SELECT x.k, x.v, y.w FROM data.{a['slug']} x JOIN data.{b['slug']} y ON x.k = y.k ORDER BY x.k LIMIT 5000"
    out = await run_readonly_query(state, sql, catalog.version, state.settings)
    assert out["rows"] == [[2, 20, 200], [3, 30, 300]]

    # delete the non-owner sheet first: the shared file must stay; the last delete removes it
    first, last = (b, a) if (a["parent_upload_id"] is None and a["id"] == (b["parent_upload_id"] or b["id"])) else (a, b)
    assert (await client.delete(f"/api/datasets/{first['id']}", headers=auth(admin_token))).status_code == 204
    assert path.exists()
    assert (await client.delete(f"/api/datasets/{last['id']}", headers=auth(admin_token))).status_code == 204
    assert not path.exists()


async def test_json_array_of_objects_becomes_one_table(client, admin_token, tmp_path):
    # The parser makes a table only from ONE array of objects (a dict with two arrays is stored as a document),
    # so multi-table JSON is not supported; this checks the single-array case end to end.
    f = tmp_path / "orders.json"
    f.write_text(json.dumps({"orders": [{"id": 1, "amt": 5}, {"id": 2, "amt": 7}]}))
    ds = await upload_and_wait(client, admin_token, [f])
    tables = [d for d in ds if d["kind"] == "table"]
    assert len(tables) == 1 and tables[0]["row_count"] == 2
    path = _file_for(client, tables[0])
    assert path.exists()
    assert (await client.delete(f"/api/datasets/{tables[0]['id']}", headers=auth(admin_token))).status_code == 204
    assert not path.exists()


async def test_json_with_two_arrays_is_not_split_into_tables(client, admin_token, tmp_path):
    f = tmp_path / "two.json"
    f.write_text(json.dumps({"a": [{"x": 1}], "b": [{"y": 2}]}))
    ds = await upload_and_wait(client, admin_token, [f])
    assert [d["kind"] for d in ds] == ["document"]
