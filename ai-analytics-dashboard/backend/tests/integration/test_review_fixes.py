"""Regression tests for findings from the final code review (need compose services)."""
import asyncio
import threading

from asgi_lifespan import LifespanManager
from sqlalchemy import select

import app.ingestion.pipeline as pipeline
import app.ingestion.tabular as tabular
from app.catalog import load_catalog
from app.db.models import Dataset
from app.llm.provider import LLMError
from helpers import auth, mailpit_code, unique_email, upload_and_wait


async def test_reregister_pending_email_does_not_replace_password(client):
    email = unique_email()
    r = await client.post("/api/auth/register", json={"email": email, "password": "VictimPw1"})
    assert r.status_code == 201
    code = await mailpit_code(email)
    r = await client.post("/api/auth/register", json={"email": email, "password": "AttackerPw1"})
    assert r.status_code == 201
    assert (await client.post("/api/auth/verify", json={"email": email, "code": code})).status_code == 200
    attacker = await client.post("/api/auth/login", json={"identifier": email, "password": "AttackerPw1"})
    victim = await client.post("/api/auth/login", json={"identifier": email, "password": "VictimPw1"})
    assert attacker.status_code == 401 and victim.status_code == 200


async def test_interrupted_ingestion_is_marked_failed_on_startup(db):
    async with db.sessionmaker() as s:
        s.add(Dataset(name="x.csv", slug="pending_stuck", source_filename="x.csv", file_type="csv", status="processing"))
        s.add(Dataset(name="y.csv", slug="pending_stuck2", source_filename="y.csv", file_type="csv", status="pending"))
        await s.commit()
    from app.main import create_app

    app = create_app()
    async with LifespanManager(app, startup_timeout=60):
        pass
    async with db.sessionmaker() as s:
        rows = (await s.scalars(select(Dataset).where(Dataset.slug.like("pending_stuck%")))).all()
    assert {r.status for r in rows} == {"failed"}
    assert all("interrupted" in r.error for r in rows)


async def test_partial_failure_keeps_loaded_parts_visible(client, admin_token, fixtures_dir, monkeypatch):
    async def broken_embed(texts):
        raise LLMError("embedding service down")

    monkeypatch.setattr(client.app.state.llm, "embed", broken_embed)
    datasets = await upload_and_wait(client, admin_token, [fixtures_dir / "annual_report_2024.pdf"])
    by_status = {}
    for d in datasets:
        by_status.setdefault(d["status"], []).append(d)
    assert [d["slug"] for d in by_status["ready"]] == ["annual_report_2024_table_p3_1"]
    assert len(by_status["failed"]) == 1 and "embedding service down" in by_status["failed"][0]["error"]
    catalog = await load_catalog(client.app.state)
    assert catalog.get("annual_report_2024_table_p3_1") is not None


async def test_query_timeout_returns_json_error(client, admin_token, fixtures_dir, monkeypatch):
    await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])

    async def hanging(**kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(client.app.state.llm, "chat_json", hanging)
    monkeypatch.setattr(client.app.state.settings, "query_timeout_s", 0.3)
    r = await client.post("/api/query", json={"question": "What is the total revenue by region?"}, headers=auth(admin_token))
    assert r.status_code == 504 and r.json()["error"]["code"] == "query_timeout"


async def test_dataframe_preparation_runs_off_the_event_loop(client, admin_token, fixtures_dir, monkeypatch):
    threads: dict[str, str] = {}
    real_infer, real_records = pipeline.infer_types, tabular.to_records

    def spy_infer(df):
        threads["infer"] = threading.current_thread().name
        return real_infer(df)

    def spy_records(df, types):
        threads["records"] = threading.current_thread().name
        return real_records(df, types)

    monkeypatch.setattr(pipeline, "infer_types", spy_infer)
    monkeypatch.setattr(tabular, "to_records", spy_records)
    datasets = await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    assert datasets[0]["status"] == "ready"
    assert threads["infer"] != "MainThread" and threads["records"] != "MainThread"
