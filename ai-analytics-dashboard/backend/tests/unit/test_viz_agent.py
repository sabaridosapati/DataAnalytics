from types import SimpleNamespace

from app.agents.types import PlanStep, StepResult
from app.agents.viz_agent import run_viz_agent
from app.llm.fake_provider import FakeLLMProvider, Rule

SQL_DEP = StepResult("s1", "sql", "ok", output={"sql": "SELECT ...", "columns": ["region", "total"], "rows": [["E", 10.0], ["W", 20.0]]})
STEP = PlanStep(id="s2", agent="viz", task="bar chart of totals", depends_on=["s1"])


def ctx(*responses):
    it = iter(responses)
    return SimpleNamespace(llm=FakeLLMProvider(rules=[Rule("viz", "", lambda u: next(it))]), settings=SimpleNamespace(), question="chart?")


def spec(**kw):
    base = {"type": "bar", "title": "Totals", "x": "region", "y": ["total"], "series": None}
    base.update(kw)
    return base


async def test_valid_bar_chart_keeps_source_sql():
    out = await run_viz_agent(STEP, [SQL_DEP], ctx(spec()))
    chart = out["chart"]
    assert chart["type"] == "bar" and chart["x"] == "region" and chart["y"] == ["total"]
    assert chart["data"] == {"columns": ["region", "total"], "rows": [["E", 10.0], ["W", 20.0]]}
    assert chart["source_sql"] == "SELECT ..."


async def test_invalid_field_retries_then_falls_back_to_table():
    c = ctx(spec(y=["nope"]), spec(x="also_nope"))
    out = await run_viz_agent(STEP, [SQL_DEP], c)
    assert out["chart"]["type"] == "table"
    assert len(c.llm.calls) == 2 and "invalid" in c.llm.calls[1][1]


async def test_retry_succeeds():
    out = await run_viz_agent(STEP, [SQL_DEP], ctx(spec(y=["nope"]), spec(type="pie")))
    assert out["chart"]["type"] == "pie"


async def test_viz_empty_rows_falls_back_to_table():
    dep = StepResult("s1", "sql", "ok", output={"sql": "q", "columns": ["region", "total"], "rows": []})
    c = ctx()
    out = await run_viz_agent(STEP, [dep], c)
    assert out["chart"]["type"] == "table" and out["chart"]["data"]["rows"] == [] and c.llm.calls == []


async def test_kpi_from_compute_scalars():
    dep = StepResult("s1", "compute", "ok", output={"columns": [], "rows": [], "scalars": {"growth_pct": 12.5}, "operations_applied": []})
    out = await run_viz_agent(STEP, [dep], ctx())
    assert out["chart"]["type"] == "kpi"
    assert out["chart"]["data"] == {"columns": ["metric", "value"], "rows": [["growth_pct", 12.5]]}
    assert out["chart"]["source_sql"] is None
