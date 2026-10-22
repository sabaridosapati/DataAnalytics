import asyncio
import os
import re
import uuid
from pathlib import Path

import duckdb
import httpx

from app.duckdb_store import upload_db_path

MAILPIT = os.environ.get("MAILPIT_API", "http://mailpit:8025")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def unique_email() -> str:
    return f"user_{uuid.uuid4().hex[:10]}@example.com"


async def mailpit_code(email: str, timeout_s: float = 10.0) -> str:
    """Return the 6-digit code from the newest email sent to `email`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    async with httpx.AsyncClient(base_url=MAILPIT, timeout=10) as m:
        while True:
            r = await m.get("/api/v1/search", params={"query": f'to:"{email}"'})
            r.raise_for_status()
            msgs = r.json().get("messages") or []
            if msgs:
                detail = (await m.get(f"/api/v1/message/{msgs[0]['ID']}")).json()
                found = re.search(r"\b(\d{6})\b", detail.get("Text", ""))
                if found:
                    return found.group(1)
            if loop.time() > deadline:
                raise AssertionError(f"No verification email found for {email}")
            await asyncio.sleep(0.3)


async def register_verified_user(client, password: str = "Passw0rd!") -> tuple[str, str]:
    email = unique_email()
    r = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    code = await mailpit_code(email)
    r = await client.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return email, r.json()["access_token"]


async def upload_and_wait(client, token: str, paths: list[Path], timeout_s: float = 90.0) -> list[dict]:
    """Upload files and wait until they (and any child datasets) are ready/failed."""
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    r = await client.post("/api/datasets/upload", files=files, headers=auth(token))
    assert r.status_code == 202, r.text
    ids = {d["id"] for d in r.json()}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        listing = (await client.get("/api/datasets", headers=auth(token))).json()
        mine = [d for d in listing if d["id"] in ids or d.get("parent_upload_id") in ids]
        if mine and all(d["status"] in ("ready", "failed") for d in mine):
            return mine
        if loop.time() > deadline:
            raise AssertionError(f"Datasets not ready in time: {mine}")
        await asyncio.sleep(0.3)


def duck_count(client, dataset: dict, slug: str | None = None) -> int:
    """Row count of a table in the dataset's per-upload DuckDB file."""
    path = upload_db_path(client.app.state.settings.duckdb_path, dataset["parent_upload_id"] or dataset["id"])
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(f'SELECT COUNT(*) FROM main."{slug or dataset["slug"]}"').fetchone()[0]
    finally:
        con.close()
