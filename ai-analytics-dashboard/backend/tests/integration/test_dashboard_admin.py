from helpers import auth, register_verified_user, upload_and_wait


async def pin_region_chart(client, token):
    body = (await client.post("/api/query", json={"question": "What is the total revenue by region?"}, headers=auth(token))).json()
    chart = body["charts"][0]
    r = await client.post("/api/dashboard/pins", json={"title": "Revenue by region", "chart": chart}, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


async def test_pin_list_refresh_delete(client, admin_token, fixtures_dir):
    await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    pin = await pin_region_chart(client, admin_token)
    assert pin["sql"].startswith("SELECT region")
    _, user_token = await register_verified_user(client)
    pins = (await client.get("/api/dashboard/pins", headers=auth(user_token))).json()
    assert [p["id"] for p in pins] == [pin["id"]]  # shared workspace
    refreshed = await client.post(f"/api/dashboard/pins/{pin['id']}/refresh", headers=auth(user_token))
    assert refreshed.status_code == 200 and len(refreshed.json()["chart"]["data"]["rows"]) == 4
    assert (await client.delete(f"/api/dashboard/pins/{pin['id']}", headers=auth(user_token))).status_code == 403
    assert (await client.delete(f"/api/dashboard/pins/{pin['id']}", headers=auth(admin_token))).status_code == 204


async def test_pin_with_tampered_sql_is_not_refreshable(client, admin_token, fixtures_dir):
    await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    chart = {"type": "table", "title": "x", "x": None, "y": [], "series": None,
             "data": {"columns": ["a"], "rows": [[1]]}, "source_sql": "DELETE FROM data.sales_2024"}
    pin = (await client.post("/api/dashboard/pins", json={"title": "evil", "chart": chart}, headers=auth(admin_token))).json()
    assert pin["sql"] is None
    r = await client.post(f"/api/dashboard/pins/{pin['id']}/refresh", headers=auth(admin_token))
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_refreshable"


async def test_admin_endpoints(client, admin_token):
    email, user_token = await register_verified_user(client)
    assert (await client.get("/api/admin/users", headers=auth(user_token))).status_code == 403
    users = (await client.get("/api/admin/users", headers=auth(admin_token))).json()
    target = next(u for u in users if u["email"] == email)
    admin = next(u for u in users if u["username"] == "admin")
    r = await client.patch(f"/api/admin/users/{admin['id']}", json={"is_active": False}, headers=auth(admin_token))
    assert r.status_code == 400  # cannot change yourself
    r = await client.patch(f"/api/admin/users/{target['id']}", json={"is_active": False}, headers=auth(admin_token))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await client.get("/api/auth/me", headers=auth(user_token))).status_code == 401
    audit = (await client.get("/api/admin/audit?limit=10&offset=0", headers=auth(admin_token))).json()
    assert audit == {"items": [], "total": 0}
