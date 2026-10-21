import json

import pytest

from app.agents.rag_agent import retrieve, run_rag_agent
from app.agents.sql_agent import run_sql_agent
from app.agents.types import AgentContext, PlanStep, StepError
from app.catalog import load_catalog
from app.llm.fake_provider import Rule
from helpers import upload_and_wait


@pytest.fixture
async def loaded(client, admin_token, fixtures_dir):
    await upload_and_wait(
        client, admin_token,
        [fixtures_dir / "sales_2024.csv", fixtures_dir / "employees.json", fixtures_dir / "annual_report_2024.pdf"],
    )
    state = client.app.state
    catalog = await load_catalog(state)
    expected = json.loads((fixtures_dir / "expected_answers.json").read_text())
    return state, catalog, expected


def make_ctx(state, catalog, question="q"):
    return AgentContext(llm=state.llm, settings=state.settings, catalog=catalog, state=state, question=question)


SQL_STEP = PlanStep(id="s1", agent="sql", task="revenue by region", datasets=["sales_2024"])


async def test_sql_agent_matches_ground_truth(loaded):
    state, catalog, expected = loaded
    state.llm.rules[:0] = [Rule("sql", "", {"sql": "SELECT region, SUM(revenue) AS total FROM sales_2024 GROUP BY region ORDER BY region", "explanation": ""})]
    out = await run_sql_agent(SQL_STEP, [], make_ctx(state, catalog))
    assert out["sql"].startswith("SELECT region, SUM(revenue) AS total FROM data.sales_2024")
    got = {r[0]: r[1] for r in out["rows"]}
    for region, total in expected["revenue_by_region"].items():
        assert got[region] == pytest.approx(total, abs=0.01)
    again = await run_sql_agent(SQL_STEP, [], make_ctx(state, catalog))
    assert again["cache_hit"] is True and again["rows"] == out["rows"]


async def test_sql_agent_repairs_bad_column_once(loaded):
    state, catalog, _ = loaded
    responses = iter([
        {"sql": "SELECT regionn FROM sales_2024", "explanation": ""},
        {"sql": "SELECT DISTINCT region FROM sales_2024 ORDER BY region", "explanation": ""},
    ])
    state.llm.rules[:0] = [Rule("sql", "", lambda u: next(responses))]
    out = await run_sql_agent(SQL_STEP, [], make_ctx(state, catalog))
    assert [r[0] for r in out["rows"]] == ["East", "North", "South", "West"]
    sql_calls = [c for c in state.llm.calls if c[0] == "sql"]
    assert "could not be resolved" in sql_calls[1][1]


async def test_malicious_sql_blocked_and_data_intact(loaded):
    state, catalog, _ = loaded
    state.llm.rules[:0] = [Rule("sql", "", {"sql": "DELETE FROM data.sales_2024", "explanation": ""})]
    with pytest.raises(StepError, match="Only SELECT"):
        await run_sql_agent(SQL_STEP, [], make_ctx(state, catalog))
    async with state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM data.sales_2024") == 2000


async def test_sql_agent_zero_rows_returns_columns(loaded):
    state, catalog, _ = loaded
    state.llm.rules[:0] = [Rule("sql", "", {"sql": "SELECT region, revenue FROM sales_2024 WHERE region = 'Nowhere'", "explanation": ""})]
    out = await run_sql_agent(SQL_STEP, [], make_ctx(state, catalog))
    assert out["columns"] == ["region", "revenue"] and out["rows"] == []


async def test_sql_agent_empty_sql_means_unanswerable(loaded):
    state, catalog, _ = loaded
    state.llm.rules[:0] = [Rule("sql", "", {"sql": "", "explanation": "no profit column"})]
    with pytest.raises(StepError, match="no profit column"):
        await run_sql_agent(SQL_STEP, [], make_ctx(state, catalog))


async def test_rag_retrieval_finds_q3_reason(loaded):
    state, catalog, _ = loaded
    doc = catalog.get("annual_report_2024")
    chunks = await retrieve(make_ctx(state, catalog), "why did Q3 revenue decline supply-chain", [doc.id])
    assert chunks and any("supply-chain" in c["content"] for c in chunks[:3])
    assert chunks[0]["source"] == "annual_report_2024.pdf"


async def test_rag_agent_drops_hallucinated_citations(loaded):
    state, catalog, _ = loaded
    step = PlanStep(id="s1", agent="rag", task="why did Q3 drop", datasets=["annual_report_2024"])
    state.llm.rules[:0] = [Rule("rag", "", {"answer": "Made up.", "found": True, "citations": [999999]})]
    out = await run_rag_agent(step, [], make_ctx(state, catalog, "why did Q3 drop?"))
    assert out["found"] is False and out["citations"] == []

    def cite_first(user):
        import re
        first = int(re.search(r'<excerpt id="(\d+)"', user).group(1))
        return {"answer": "Supply-chain disruptions.", "found": True, "citations": [first, 999999]}

    state.llm.rules[:0] = [Rule("rag", "", cite_first)]
    out = await run_rag_agent(step, [], make_ctx(state, catalog, "why did Q3 drop?"))
    assert out["found"] is True and len(out["citations"]) == 1
    assert out["citations"][0]["page"] in (1, 2, 3)
