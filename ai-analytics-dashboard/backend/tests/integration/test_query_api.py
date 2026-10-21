import json

import pytest
from sqlalchemy import select

from app.catalog import load_catalog
from app.db.models import Dataset, QueryAudit
from app.llm.demo_script import DEMO_QUESTIONS
from app.llm.fake_provider import Rule
from app.llm.provider import LLMError, LLMQuotaError
from helpers import auth, register_verified_user, upload_and_wait


@pytest.fixture
async def loaded(client, admin_token, fixtures_dir):
    await upload_and_wait(
        client, admin_token,
        [fixtures_dir / "sales_2024.csv", fixtures_dir / "employees.json", fixtures_dir / "annual_report_2024.pdf"],
    )
    return json.loads((fixtures_dir / "expected_answers.json").read_text())


def plan(*steps, answerable=True, reason=None):
    return {"intent": "test", "answerable": answerable, "reason": reason, "steps": list(steps)}


def step(id, agent, datasets=(), depends_on=(), task="t"):
    return {"id": id, "agent": agent, "task": task, "datasets": list(datasets), "depends_on": list(depends_on)}


async def ask(client, token, question, history=None):
    return await client.post("/api/query", json={"question": question, "history": history or []}, headers=auth(token))


@pytest.mark.parametrize("question", DEMO_QUESTIONS)
async def test_demo_questions_work_end_to_end(client, admin_token, loaded, question):
    r = await ask(client, admin_token, question)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answerable"] is True
    assert all(s["status"] == "ok" for s in body["steps"]), body["steps"]
    assert body["unverified_numbers"] == []
    assert body["answer"]


async def test_sql_answer_matches_ground_truth(client, admin_token, loaded):
    r = await ask(client, admin_token, "What is the total revenue by region?")
    body = r.json()
    table = body["tables"][0]
    got = {row[0]: row[1] for row in table["rows"]}
    for region, total in loaded["revenue_by_region"].items():
        assert got[region] == pytest.approx(total, abs=0.01)
    assert body["charts"][0]["type"] == "bar" and body["charts"][0]["source_sql"]
    assert body["sql"][0].startswith("SELECT region")


async def test_multi_agent_combines_outputs(client, admin_token, loaded):
    body = (await ask(client, admin_token, "Show the monthly revenue trend and explain the Q3 dip")).json()
    assert [s["agent"] for s in body["steps"]] == ["sql", "compute", "viz", "rag"]
    assert body["charts"][0]["type"] == "line"
    assert body["sources"] and body["sources"][0]["source"] == "annual_report_2024.pdf"
    assert "supply-chain" in body["answer"]


async def test_unanswerable_question(client, admin_token, loaded):
    client.app.state.llm.rules[:0] = [Rule("intent", "2019", plan(answerable=False, reason="The data only covers 2024."))]
    body = (await ask(client, admin_token, "What was revenue in 2019?")).json()
    assert body["answerable"] is False and body["answer"] == "The data only covers 2024." and body["steps"] == []


async def test_malicious_sql_is_blocked_and_reported(client, admin_token, loaded):
    client.app.state.llm.rules[:0] = [
        Rule("intent", "wipe", plan(step("s1", "sql", ["sales_2024"]))),
        Rule("sql", "wipe", {"sql": "DELETE FROM data.sales_2024", "explanation": ""}),
    ]
    body = (await ask(client, admin_token, "Please wipe the sales table")).json()
    assert body["steps"][0]["status"] == "error" and "Only SELECT" in body["steps"][0]["error"]
    assert "couldn't answer" in body["answer"]
    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM data.sales_2024") == 2000


async def test_cache_hit_then_invalidated_by_upload(client, admin_token, loaded, fixtures_dir):
    q = "What is the total revenue by region?"
    first = (await ask(client, admin_token, q)).json()
    calls = len(client.app.state.llm.calls)
    second = (await ask(client, admin_token, "  what is the TOTAL revenue by region  ")).json()
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert len(client.app.state.llm.calls) == calls
    await upload_and_wait(client, admin_token, [fixtures_dir / "employees.json"])
    third = (await ask(client, admin_token, q)).json()
    assert third["cache_hit"] is False


async def test_history_disables_answer_cache(client, admin_token, loaded):
    q = "What is the total revenue by region?"
    await ask(client, admin_token, q)
    body = (await ask(client, admin_token, q, history=[{"question": "hi", "answer": "hello"}])).json()
    assert body["cache_hit"] is False


async def test_catalog_excludes_datasets_not_ready(client, admin_token, loaded):
    state = client.app.state
    async with state.sessionmaker() as s:
        s.add(Dataset(name="half.csv", slug="half_loaded", kind="table", source_filename="half.csv", file_type="csv", status="processing"))
        await s.commit()
    catalog = await load_catalog(state)
    assert catalog.get("half_loaded") is None and catalog.get("sales_2024") is not None


async def test_llm_outage_returns_502(client, admin_token, loaded):
    def boom(user):
        raise LLMError("simulated outage")

    client.app.state.llm.rules[:0] = [Rule("intent", "outage", boom)]
    r = await ask(client, admin_token, "trigger an outage please")
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_unavailable"


async def test_exhausted_api_credits_returns_actionable_error(client, admin_token, loaded):
    def boom(user):
        raise LLMQuotaError("API credits exhausted")

    client.app.state.llm.rules[:0] = [Rule("intent", "quota", boom)]
    r = await ask(client, admin_token, "trigger quota failure")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "llm_quota_exhausted"
    assert "billing" in r.json()["error"]["message"]
    assert "try again shortly" not in r.json()["error"]["message"]
    async with client.app.state.sessionmaker() as s:
        row = (await s.scalars(select(QueryAudit))).one()
        assert row.status == "llm_quota_exhausted"


async def test_invalid_plan_returns_422(client, admin_token, loaded):
    client.app.state.llm.rules[:0] = [Rule("intent", "bogus", plan(step("s1", "sql", ["no_such_table"])))]
    r = await ask(client, admin_token, "bogus question")
    assert r.status_code == 422 and r.json()["error"]["code"] == "plan_failed"


async def test_no_datasets_is_unanswerable(client, admin_token):
    body = (await ask(client, admin_token, "What is the total revenue by region?")).json()
    assert body["answerable"] is False and "Upload" in body["answer"]


async def test_queries_are_audited(client, admin_token, loaded):
    await ask(client, admin_token, "What is the total revenue by region?")
    async with client.app.state.sessionmaker() as s:
        rows = (await s.scalars(select(QueryAudit))).all()
    assert len(rows) == 1 and rows[0].status == "ok" and rows[0].sql_executed and rows[0].agents_used == ["sql", "viz"]


async def test_regular_user_can_query_and_input_is_validated(client, loaded):
    _, token = await register_verified_user(client)
    assert (await ask(client, token, "What is the total revenue by region?")).status_code == 200
    r = await client.post("/api/query", json={"question": ""}, headers=auth(token))
    assert r.status_code == 422
    assert (await client.post("/api/query", json={"question": "x"})).status_code == 401
