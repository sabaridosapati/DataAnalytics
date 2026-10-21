from types import SimpleNamespace

from app.agents.aggregator import aggregate
from app.agents.types import Plan, StepResult
from app.llm.fake_provider import FakeLLMProvider, Rule

PLAN = Plan.model_validate(
    {
        "intent": "x",
        "answerable": True,
        "reason": None,
        "steps": [
            {"id": "s1", "agent": "sql", "task": "totals", "datasets": ["sales"], "depends_on": []},
            {"id": "s2", "agent": "viz", "task": "chart", "datasets": [], "depends_on": ["s1"]},
            {"id": "s3", "agent": "rag", "task": "why", "datasets": ["doc"], "depends_on": []},
        ],
    }
)
CHART = {"type": "bar", "title": "t", "x": "region", "y": ["total"], "series": None, "data": {"columns": ["region", "total"], "rows": [["E", 10.5]]}, "source_sql": "SELECT 1"}
RESULTS = {
    "s1": StepResult("s1", "sql", "ok", output={"sql": "SELECT 1", "columns": ["region", "total"], "rows": [["E", 10.5]], "row_count": 1, "truncated": False, "cache_hit": False}, ms=5),
    "s2": StepResult("s2", "viz", "ok", output={"chart": CHART}, ms=1),
    "s3": StepResult("s3", "rag", "ok", output={"answer": "Supply issues.", "found": True, "citations": [{"chunk_id": 1, "source": "r.pdf", "page": 2, "snippet": "..."}]}, ms=3),
}


def ctx(*answers):
    it = iter(answers)
    return SimpleNamespace(llm=FakeLLMProvider(rules=[Rule("aggregate", "", lambda u: next(it))]), settings=SimpleNamespace(), question="q")


async def test_assembles_response():
    c = ctx("East revenue was 10.5 due to supply issues.")
    resp = await aggregate("Revenue by region?", PLAN, RESULTS, c)
    assert resp["answer"].startswith("East revenue was 10.5")
    assert resp["unverified_numbers"] == []
    assert resp["charts"] == [CHART]
    assert resp["tables"][0]["columns"] == ["region", "total"] and resp["tables"][0]["title"] == "totals"
    assert resp["sources"][0]["source"] == "r.pdf"
    assert resp["sql"] == ["SELECT 1"]
    assert [s["id"] for s in resp["steps"]] == ["s1", "s2", "s3"]
    assert resp["plan"]["steps"][0]["agent"] == "sql"
    assert "User question: Revenue by region?" in c.llm.calls[0][1]


async def test_invented_number_regenerates_then_flags():
    c = ctx("East revenue was 999.", "East revenue was 10.5.")
    resp = await aggregate("q", PLAN, RESULTS, c)
    assert resp["answer"] == "East revenue was 10.5." and resp["unverified_numbers"] == []
    assert "not found in the outputs" in c.llm.calls[1][1]

    c = ctx("East revenue was 999.", "Still 888.")
    resp = await aggregate("q", PLAN, RESULTS, c)
    assert resp["unverified_numbers"] == ["888"]


async def test_all_failed_skips_llm():
    failed = {
        "s1": StepResult("s1", "sql", "error", error="Only SELECT queries are allowed."),
        "s2": StepResult("s2", "viz", "skipped", error="skipped because step(s) s1 did not succeed"),
        "s3": StepResult("s3", "rag", "error", error="timed out"),
    }
    c = ctx()
    resp = await aggregate("q", PLAN, failed, c)
    assert "couldn't answer" in resp["answer"] and "Only SELECT" in resp["answer"]
    assert c.llm.calls == [] and resp["charts"] == []
