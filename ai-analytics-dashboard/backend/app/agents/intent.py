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
            purpose="intent", system=SYSTEM, user=prompt, schema=PLAN_SCHEMA, model=settings.planner_model
        )
        try:
            plan = Plan.model_validate(raw)
            validate_plan(plan, catalog)
            return plan
        except (ValidationError, PlanValidationError) as exc:
            error = str(exc)
    raise PlanValidationError(f"Could not build a valid plan for this question: {error}")
