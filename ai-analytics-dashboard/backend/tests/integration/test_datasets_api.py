from helpers import auth, register_verified_user, upload_and_wait


async def test_upload_three_fixtures(client, admin_token, fixtures_dir):
    ds = await upload_and_wait(
        client,
        admin_token,
        [fixtures_dir / "sales_2024.csv", fixtures_dir / "employees.json", fixtures_dir / "annual_report_2024.pdf"],
    )
    assert all(d["status"] == "ready" for d in ds), ds
    by_slug = {d["slug"]: d for d in ds}
    assert by_slug["sales_2024"]["kind"] == "table" and by_slug["sales_2024"]["row_count"] == 2000
    assert by_slug["employees"]["row_count"] == 200
    assert by_slug["annual_report_2024"]["kind"] == "document" and by_slug["annual_report_2024"]["chunk_count"] >= 3
    kpi = by_slug["annual_report_2024_table_p3_1"]
    assert kpi["kind"] == "table" and kpi["row_count"] == 4
    assert kpi["parent_upload_id"] == by_slug["annual_report_2024"]["id"] or by_slug["annual_report_2024"]["parent_upload_id"] == kpi["id"]

    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM data.sales_2024") == 2000

    detail = (await client.get(f"/api/datasets/{by_slug['employees']['id']}", headers=auth(admin_token))).json()
    types = {c["column_name"]: c["pg_type"] for c in detail["columns"]}
    assert types["salary"] == "bigint" and types["hire_date"] == "date" and types["address_city"] == "text"
    assert len(detail["preview"]["rows"]) == 20 and "salary" in detail["preview"]["columns"]

    doc = (await client.get(f"/api/datasets/{by_slug['annual_report_2024']['id']}", headers=auth(admin_token))).json()
    assert doc["preview"]["chunks"][0]["metadata"]["file"] == "annual_report_2024.pdf"


async def test_same_filename_twice_gets_unique_slug(client, admin_token, fixtures_dir):
    first = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    second = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    assert first[0]["slug"] == "sales_2024" and second[0]["slug"] == "sales_2024_2"
    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM data.sales_2024_2") == 2000


async def test_regular_user_can_upload_text(client):
    _, token = await register_verified_user(client)
    files = [("files", ("notes.txt", b"Customers complained about late deliveries in August.", "text/plain"))]
    r = await client.post("/api/datasets/upload", files=files, headers=auth(token))
    assert r.status_code == 202
    listing = (await client.get("/api/datasets", headers=auth(token))).json()
    assert listing[0]["status"] == "ready" and listing[0]["kind"] == "document"


async def test_unsupported_and_mismatched_files_rejected(client, admin_token):
    r = await client.post("/api/datasets/upload", files=[("files", ("evil.exe", b"MZ\x90", "application/octet-stream"))], headers=auth(admin_token))
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_file"
    r = await client.post("/api/datasets/upload", files=[("files", ("fake.pdf", b"hello", "application/pdf"))], headers=auth(admin_token))
    assert r.status_code == 415
    assert (await client.get("/api/datasets", headers=auth(admin_token))).json() == []


async def test_too_large_rejected(client, admin_token, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, "max_upload_mb", 0)
    r = await client.post("/api/datasets/upload", files=[("files", ("a.csv", b"a,b\n1,2\n", "text/csv"))], headers=auth(admin_token))
    assert r.status_code == 413


async def test_bad_content_marks_dataset_failed(client, admin_token):
    files = [("files", ("broken.json", b"{not json", "application/json"))]
    r = await client.post("/api/datasets/upload", files=files, headers=auth(admin_token))
    assert r.status_code == 202
    d = (await client.get("/api/datasets", headers=auth(admin_token))).json()[0]
    assert d["status"] == "failed" and "Invalid JSON" in d["error"]


async def test_delete_requires_admin_and_drops_table(client, admin_token, fixtures_dir):
    ds = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    _, user_token = await register_verified_user(client)
    r = await client.delete(f"/api/datasets/{ds[0]['id']}", headers=auth(user_token))
    assert r.status_code == 403
    r = await client.delete(f"/api/datasets/{ds[0]['id']}", headers=auth(admin_token))
    assert r.status_code == 204
    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('data.sales_2024')") is None
    assert (await client.get("/api/datasets", headers=auth(admin_token))).json() == []


async def test_requires_auth(client):
    assert (await client.get("/api/datasets")).status_code == 401
