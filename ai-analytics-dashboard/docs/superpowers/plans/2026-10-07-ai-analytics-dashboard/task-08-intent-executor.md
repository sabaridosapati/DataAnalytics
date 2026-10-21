# Task 8: Plan types, intent analyzer, DAG executor

**Files:**
- Create: `backend/app/agents/__init__.py` (empty for now; agent registry added in Task 9), `backend/app/agents/types.py`, `backend/app/agents/intent.py`, `backend/app/agents/executor.py`
- Test: `backend/tests/unit/test_intent.py`, `backend/tests/unit/test_executor.py`

**Interfaces:**
- Consumes: `app.catalog.Catalog/DatasetInfo/ColumnInfo`; `app.llm.provider.LLMError`.
- Produces `app.agents.types`:
  - `PlanStep(id: str, agent: Literal["sql","rag","compute","viz"], task: str, datasets: list[str]=[], depends_on: list[str]=[])` (Pydantic)
  - `Plan(intent: str, answerable: bool, reason: str | None = None, steps: list[PlanStep] = [])` (Pydantic)
  - `StepResult(id, agent, status: "ok"|"error"|"skipped", output: dict|None=None, error: str|None=None, ms: int=0)` (dataclass)
  - `StepError(Exception)` — user-facing agent failure message
  - `AgentContext(llm, settings, catalog, state, question)` (dataclass)
  - `AgentFn = Callable[[PlanStep, list[StepResult], AgentContext], Awaitable[dict]]`
- Produces `app.agents.intent`: `PLAN_SCHEMA`, `PlanValidationError`, `validate_plan(plan, catalog)`, `async analyze_intent(question, history, catalog, llm, settings) -> Plan` (one re-plan on invalid output; `answerable=False` when no datasets).
- Produces `app.agents.executor.async execute_plan(plan, agents: dict[str, AgentFn], ctx, timeout_s) -> dict[str, StepResult]` — parallel by dependency level, dependents of failed steps are `skipped`.

- [ ] **Step 1: Write the failing tests**

**File: `backend/tests/unit/test_intent.py`**
```python
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
```

**File: `backend/tests/unit/test_executor.py`**
```python
import asyncio
import time

from app.agents.executor import execute_plan
from app.agents.types import Plan, StepError


def make_plan(*steps):
    return Plan.model_validate({"intent": "x", "answerable": True, "reason": None, "steps": list(steps)})


def step(id, agent, depends_on=()):
    return {"id": id, "agent": agent, "task": id, "datasets": [], "depends_on": list(depends_on)}


async def slow(step, deps, ctx):
    await asyncio.sleep(0.3)
    return {"value": step.id}


async def echo_deps(step, deps, ctx):
    return {"got": [d.output["value"] for d in deps]}


async def fail(step, deps, ctx):
    raise StepError("boom")


async def crash(step, deps, ctx):
    raise ZeroDivisionError("bad")


async def test_independent_steps_run_in_parallel():
    plan = make_plan(step("s1", "sql"), step("s2", "rag"))
    t0 = time.perf_counter()
    results = await execute_plan(plan, {"sql": slow, "rag": slow}, None, 5)
    assert time.perf_counter() - t0 < 0.55
    assert results["s1"].status == "ok" and results["s2"].output == {"value": "s2"}


async def test_dependencies_receive_outputs_in_order():
    plan = make_plan(step("s1", "sql"), step("s2", "rag"), step("s3", "compute", ["s1", "s2"]))
    results = await execute_plan(plan, {"sql": slow, "rag": slow, "compute": echo_deps}, None, 5)
    assert results["s3"].output == {"got": ["s1", "s2"]}


async def test_failed_step_skips_dependents_but_not_independents():
    plan = make_plan(step("s1", "sql"), step("s2", "viz", ["s1"]), step("s3", "rag"))
    results = await execute_plan(plan, {"sql": fail, "viz": echo_deps, "rag": slow}, None, 5)
    assert results["s1"].status == "error" and results["s1"].error == "boom"
    assert results["s2"].status == "skipped" and "s1" in results["s2"].error
    assert results["s3"].status == "ok"


async def test_timeout_and_unexpected_exception():
    plan = make_plan(step("s1", "sql"), step("s2", "rag"))
    results = await execute_plan(plan, {"sql": slow, "rag": crash}, None, 0.05)
    assert results["s1"].status == "error" and "timed out" in results["s1"].error
    assert results["s2"].status == "error" and "ZeroDivisionError" in results["s2"].error
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit/test_intent.py tests/unit/test_executor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents'`.

- [ ] **Step 3: Implement types**

**File: `backend/app/agents/__init__.py`**
```python
```

**File: `backend/app/agents/types.py`**
```python
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

AgentName = Literal["sql", "rag", "compute", "viz"]


class PlanStep(BaseModel):
    id: str
    agent: AgentName
    task: str
    datasets: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    intent: str
    answerable: bool
    reason: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)


@dataclass
class StepResult:
    id: str
    agent: str
    status: Literal["ok", "error", "skipped"]
    output: dict | None = None
    error: str | None = None
    ms: int = 0


class StepError(Exception):
    """An agent could not complete its step; the message is shown to the user."""


@dataclass
class AgentContext:
    llm: Any
    settings: Any
    catalog: Any
    state: Any
    question: str


AgentFn = Callable[[PlanStep, list[StepResult], AgentContext], Awaitable[dict]]
```

- [ ] **Step 4: Implement the intent analyzer**

**File: `backend/app/agents/intent.py`**
```python
from pydantic import ValidationError

from app.agents.types import Plan
from app.catalog import Catalog

MAX_STEPS = 6

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "answerable", "reason", "steps"],
    "properties": {
        "intent": {"type": "string"},
        "answerable": {"type": "boolean"},
        "reason": {"type": ["string", "null"]},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "agent", "task", "datasets", "depends_on"],
                "properties": {
                    "id": {"type": "string"},
                    "agent": {"type": "string", "enum": ["sql", "rag", "compute", "viz"]},
                    "task": {"type": "string"},
                    "datasets": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

SYSTEM = """You are the intent analyzer of an analytics assistant. Turn the user's question into a plan of agent steps.

Agents:
- sql: queries TABLE datasets (filter, aggregate, join, rank). Needs `datasets` = table slugs.
- rag: answers from DOCUMENT datasets (explanations, reasons, qualitative facts). Needs `datasets` = document slugs.
- compute: arithmetic/statistics on the output of earlier steps (growth, % change, ratios, moving averages,
  percentiles, correlations). `datasets` = [], must have `depends_on`.
- viz: builds a chart from the output of an earlier step. `datasets` = [], must have `depends_on`.

Rules:
- Use ONLY dataset slugs listed in the catalog. Never invent datasets or columns.
- Step ids are s1, s2, ... ; at most 6 steps; `depends_on` only references earlier ids.
- Each step's `task` is a precise, self-contained instruction for that agent.
- Add a viz step when the user asks for a chart, trend, comparison or distribution.
- Prefer letting sql do aggregation; use compute for math that SQL output must feed into.
- If the catalog cannot answer the question, set answerable=false, steps=[], and explain in `reason`.
- Otherwise set answerable=true and reason=null."""


class PlanValidationError(ValueError):
    pass


def validate_plan(plan: Plan, catalog: Catalog) -> None:
    if not plan.answerable:
        return
    if not plan.steps:
        raise PlanValidationError("The plan has no steps.")
    if len(plan.steps) > MAX_STEPS:
        raise PlanValidationError(f"Plans may have at most {MAX_STEPS} steps.")
    ids = [s.id for s in plan.steps]
    if len(set(ids)) != len(ids):
        raise PlanValidationError("Duplicate step id in plan.")
    for s in plan.steps:
        for dep in s.depends_on:
            if dep not in ids or dep == s.id:
                raise PlanValidationError(f"Step {s.id} depends on unknown step '{dep}'.")
        for slug in s.datasets:
            info = catalog.get(slug)
            if info is None:
                raise PlanValidationError(f"Unknown dataset '{slug}' in step {s.id}.")
            if s.agent == "sql" and info.kind != "table":
                raise PlanValidationError(f"Step {s.id}: sql steps must use a table dataset, '{slug}' is a {info.kind}.")
            if s.agent == "rag" and info.kind != "document":
                raise PlanValidationError(f"Step {s.id}: rag steps must use a document dataset, '{slug}' is a {info.kind}.")
        if s.agent in ("sql", "rag") and not s.datasets:
            raise PlanValidationError(f"Step {s.id} ({s.agent}) needs at least one dataset.")
        if s.agent in ("compute", "viz") and not s.depends_on:
            raise PlanValidationError(f"Step {s.id} ({s.agent}) must depend on an earlier step.")
    deps = {s.id: set(s.depends_on) for s in plan.steps}
    resolved: set[str] = set()
    while len(resolved) < len(deps):
        ready = [i for i, d in deps.items() if i not in resolved and d <= resolved]
        if not ready:
            raise PlanValidationError("The plan contains a dependency cycle.")
        resolved.update(ready)


def _user_prompt(question: str, history: list[dict], catalog: Catalog) -> str:
    parts = [f"Catalog:\n{catalog.summary()}"]
    if history:
        turns = "\n".join(f"Previous question: {h['question']}\nPrevious answer: {h['answer'][:500]}" for h in history[-3:])
        parts.append(f"Conversation so far:\n{turns}")
    parts.append(f"User question: {question}")
    return "\n\n".join(parts)


async def analyze_intent(question: str, history: list[dict], catalog: Catalog, llm, settings) -> Plan:
    if not catalog.datasets:
        return Plan(
            intent="no_data",
            answerable=False,
            reason="No datasets are available yet. Upload data on the Datasets page first.",
        )
    base = _user_prompt(question, history, catalog)
    error: str | None = None
    for _ in range(2):
        prompt = base if error is None else f"{base}\n\nYour previous plan was invalid: {error}\nReturn a corrected plan."
        raw = await llm.chat_json(
            purpose="intent", system=SYSTEM, user=prompt, schema=PLAN_SCHEMA, model=settings.openai_planner_model
        )
        try:
            plan = Plan.model_validate(raw)
            validate_plan(plan, catalog)
            return plan
        except (ValidationError, PlanValidationError) as exc:
            error = str(exc)
    raise PlanValidationError(f"Could not build a valid plan for this question: {error}")
```

- [ ] **Step 5: Implement the executor**

**File: `backend/app/agents/executor.py`**
```python
import asyncio
import logging
import time

from app.agents.types import AgentContext, AgentFn, Plan, PlanStep, StepError, StepResult
from app.llm.provider import LLMError

log = logging.getLogger(__name__)


async def _run(step: PlanStep, deps: list[StepResult], agents: dict[str, AgentFn], ctx: AgentContext, timeout_s: float) -> StepResult:
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        output = await asyncio.wait_for(agents[step.agent](step, deps, ctx), timeout=timeout_s)
        return StepResult(step.id, step.agent, "ok", output=output, ms=elapsed())
    except asyncio.TimeoutError:
        return StepResult(step.id, step.agent, "error", error=f"timed out after {timeout_s}s", ms=elapsed())
    except StepError as exc:
        return StepResult(step.id, step.agent, "error", error=str(exc), ms=elapsed())
    except LLMError as exc:
        return StepResult(step.id, step.agent, "error", error=f"language model unavailable: {exc}", ms=elapsed())
    except Exception as exc:  # noqa: BLE001
        log.exception("Agent %s crashed on step %s", step.agent, step.id)
        return StepResult(step.id, step.agent, "error", error=f"internal error ({type(exc).__name__})", ms=elapsed())


async def execute_plan(plan: Plan, agents: dict[str, AgentFn], ctx: AgentContext, timeout_s: float) -> dict[str, StepResult]:
    results: dict[str, StepResult] = {}
    pending = {s.id: s for s in plan.steps}
    while pending:
        ready = [s for s in pending.values() if all(d in results for d in s.depends_on)]
        if not ready:
            for s in pending.values():
                results[s.id] = StepResult(s.id, s.agent, "skipped", error="unresolvable dependency")
            break
        runnable: list[PlanStep] = []
        for s in ready:
            del pending[s.id]
            failed = [d for d in s.depends_on if results[d].status != "ok"]
            if failed:
                results[s.id] = StepResult(s.id, s.agent, "skipped", error=f"skipped because step(s) {', '.join(failed)} did not succeed")
            else:
                runnable.append(s)
        outcomes = await asyncio.gather(
            *(_run(s, [results[d] for d in s.depends_on], agents, ctx, timeout_s) for s in runnable)
        )
        for r in outcomes:
            results[r.id] = r
    return results
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend
git commit -m "feat(agents): typed plans, intent analyzer with validation, DAG executor"
```
