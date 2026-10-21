import math
from types import SimpleNamespace

import pandas as pd
import pytest

from app.agents.compute_agent import apply_operations, run_compute_agent
from app.agents.types import PlanStep, StepError, StepResult
from app.llm.fake_provider import FakeLLMProvider, Rule


def op(name, **kw):
    base = {"op": name, "column": None, "columns": [], "group_by": [], "agg": None, "expression": None, "alias": None, "window": None, "q": None}
    base.update(kw)
    return base


DF = pd.DataFrame({"month": ["2024-01", "2024-02", "2024-03"], "revenue": [100.0, 150.0, 120.0], "cost": [60.0, 90.0, 60.0]})


def test_scalar_aggregates():
    _, scalars, applied = apply_operations(DF, [op("sum", column="revenue"), op("mean", column="revenue", alias="avg_rev"), op("count", column="revenue")])
    assert scalars == {"sum_revenue": 370.0, "avg_rev": pytest.approx(123.3333, rel=1e-4), "count_revenue": 3}
    assert len(applied) == 3


def test_pct_change_growth_ratio_moving_avg_rank():
    df, scalars, _ = apply_operations(
        DF,
        [
            op("pct_change", column="revenue", alias="mom"),
            op("growth", column="revenue"),
            op("ratio", columns=["cost", "revenue"], alias="cost_ratio"),
            op("moving_avg", column="revenue", window=2, alias="ma2"),
            op("rank", column="revenue", alias="rk"),
        ],
    )
    assert math.isnan(df["mom"][0]) and df["mom"][1] == pytest.approx(50.0)
    assert scalars["revenue_growth_pct"] == pytest.approx(20.0)
    assert df["cost_ratio"][0] == pytest.approx(0.6)
    assert df["ma2"][1] == pytest.approx(125.0)
    assert list(df["rk"]) == [3, 1, 2]


def test_group_agg_percentile_corr_expression():
    sales = pd.DataFrame({"region": ["E", "E", "W"], "revenue": [1.0, 3.0, 5.0], "units": [1, 3, 5]})
    df, scalars, _ = apply_operations(
        sales,
        [
            op("percentile", column="revenue", q=0.5),
            op("corr", columns=["revenue", "units"]),
            op("expression", expression="revenue / units * 100", alias="per_unit"),
            op("group_agg", group_by=["region"], column="revenue", agg="sum", alias="total"),
        ],
    )
    assert scalars["revenue_p50"] == 3.0 and scalars["revenue_units_corr"] == pytest.approx(1.0)
    assert list(df.columns) == ["region", "total"] and list(df["total"]) == [4.0, 5.0]


def test_errors_are_step_errors():
    with pytest.raises(StepError, match="not found"):
        apply_operations(DF, [op("sum", column="nope")])
    with pytest.raises(StepError, match="Disallowed|Unknown name"):
        apply_operations(DF, [op("expression", expression="__import__('os')", alias="x")])
    with pytest.raises(StepError, match="two columns"):
        apply_operations(DF, [op("ratio", columns=["cost"])])


async def test_run_compute_agent_uses_dependency_table():
    llm = FakeLLMProvider(rules=[Rule("compute", "", {"operations": [op("sum", column="revenue", alias="total")]})])
    ctx = SimpleNamespace(llm=llm, settings=SimpleNamespace(gemini_chat_model="m", max_sql_rows=5000), question="total?")
    dep = StepResult("s1", "sql", "ok", output={"columns": ["month", "revenue"], "rows": [["a", 1.5], ["b", 2.5]]})
    step = PlanStep(id="s2", agent="compute", task="sum revenue", depends_on=["s1"])
    out = await run_compute_agent(step, [dep], ctx)
    assert out["scalars"] == {"total": 4.0} and out["rows"] == [["a", 1.5], ["b", 2.5]]
    assert "User question: total?" in llm.calls[0][1]


async def test_run_compute_agent_without_table_fails():
    ctx = SimpleNamespace(llm=FakeLLMProvider(), settings=SimpleNamespace(), question="q")
    dep = StepResult("s1", "rag", "ok", output={"answer": "x"})
    with pytest.raises(StepError):
        await run_compute_agent(PlanStep(id="s2", agent="compute", task="t", depends_on=["s1"]), [dep], ctx)
