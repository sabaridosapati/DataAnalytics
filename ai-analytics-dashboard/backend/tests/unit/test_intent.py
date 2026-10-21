import pytest

from app.agents.intent import PlanValidationError, analyze_intent, validate_plan
from app.agents.types import Plan
from app.catalog import Catalog, ColumnInfo, DatasetInfo
from app.config import Settings
from app.llm.fake_provider import FakeLLMProvider, Rule

CATALOG = Catalog(
    version=1,
    datasets=[
        DatasetInfo(1, "sales_2024", "sales_2024.csv", "table", [ColumnInfo("region", "text"), ColumnInfo("revenue", "double precision")], 2000),
        DatasetInfo(2, "annual_report_2024", "annual_report_2024.pdf", "document", [], 0, 5),
    ],
)
SETTINGS = Settings()


def plan(*steps, answerable=True):
    return Plan.model_validate({"intent": "x", "answerable": answerable, "reason": None, "steps": list(steps)})


def step(id, agent, datasets=(), depends_on=()):
    return {"id": id, "agent": agent, "task": "t", "datasets": list(datasets), "depends_on": list(depends_on)}


def test_valid_multi_agent_plan():
    validate_plan(
        plan(
            step("s1", "sql", ["sales_2024"]),
            step("s2", "compute", depends_on=["s1"]),
            step("s3", "viz", depends_on=["s2"]),
            step("s4", "rag", ["annual_report_2024"]),
        ),
        CATALOG,
    )


@pytest.mark.parametrize(
    "steps, message",
    [
        ([step("s1", "sql", ["nope"])], "Unknown dataset"),
        ([step("s1", "sql", ["annual_report_2024"])], "table dataset"),
        ([step("s1", "rag", ["sales_2024"])], "document dataset"),
        ([step("s1", "sql")], "needs at least one dataset"),
        ([step("s1", "compute")], "must depend on"),
        ([step("s1", "sql", ["sales_2024"]), step("s2", "viz", depends_on=["s9"])], "unknown step"),
        ([step("s1", "sql", ["sales_2024"]), step("s1", "sql", ["sales_2024"])], "Duplicate step id"),
        ([step("s1", "compute", depends_on=["s2"]), step("s2", "compute", depends_on=["s1"])], "cycle"),
        ([step(f"s{i}", "sql", ["sales_2024"]) for i in range(7)], "at most 6"),
        ([], "no steps"),
    ],
)
def test_invalid_plans(steps, message):
    with pytest.raises(PlanValidationError, match=message):
        validate_plan(plan(*steps), CATALOG)


def test_unanswerable_plan_needs_no_steps():
    validate_plan(plan(answerable=False), CATALOG)


async def test_analyze_intent_returns_valid_plan():
    good = {"intent": "agg", "answerable": True, "reason": None, "steps": [step("s1", "sql", ["sales_2024"])]}
    llm = FakeLLMProvider(rules=[Rule("intent", "", good)])
    result = await analyze_intent("Total revenue by region?", [], CATALOG, llm, SETTINGS)
    assert result.steps[0].agent == "sql"
    purpose, prompt = llm.calls[0]
    assert purpose == "intent"
    assert "User question: Total revenue by region?" in prompt and "sales_2024" in prompt


async def test_analyze_intent_replans_once_after_invalid_output():
    bad = {"intent": "agg", "answerable": True, "reason": None, "steps": [step("s1", "sql", ["missing"])]}
    good = {"intent": "agg", "answerable": True, "reason": None, "steps": [step("s1", "sql", ["sales_2024"])]}
    responses = iter([bad, good])
    llm = FakeLLMProvider(rules=[Rule("intent", "", lambda user: next(responses))])
    result = await analyze_intent("q", [], CATALOG, llm, SETTINGS)
    assert result.steps[0].datasets == ["sales_2024"]
    assert "previous plan was invalid" in llm.calls[1][1]


async def test_analyze_intent_gives_up_after_two_invalid_plans():
    bad = {"intent": "agg", "answerable": True, "reason": None, "steps": [step("s1", "sql", ["missing"])]}
    llm = FakeLLMProvider(rules=[Rule("intent", "", bad)])
    with pytest.raises(PlanValidationError):
        await analyze_intent("q", [], CATALOG, llm, SETTINGS)


async def test_no_datasets_is_unanswerable_without_llm_call():
    llm = FakeLLMProvider()
    result = await analyze_intent("q", [], Catalog(version=1), llm, SETTINGS)
    assert result.answerable is False and "Upload" in result.reason and llm.calls == []


async def test_history_included_in_prompt():
    good = {"intent": "x", "answerable": False, "reason": "no", "steps": []}
    llm = FakeLLMProvider(rules=[Rule("intent", "", good)])
    await analyze_intent("and for West?", [{"question": "Revenue for East?", "answer": "East: 10"}], CATALOG, llm, SETTINGS)
    assert "Revenue for East?" in llm.calls[0][1]
