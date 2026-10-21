from app.agents import AGENTS
from app.agents.aggregator import aggregate
from app.agents.executor import execute_plan
from app.agents.intent import analyze_intent
from app.agents.types import AgentContext
from app.catalog import load_catalog


def _empty_response(answer: str, plan: dict | None) -> dict:
    return {
        "answer": answer,
        "answerable": False,
        "unverified_numbers": [],
        "charts": [],
        "tables": [],
        "sources": [],
        "sql": [],
        "plan": plan,
        "steps": [],
    }


async def answer_question(state, question: str, history: list[dict]) -> dict:
    catalog = await load_catalog(state)
    plan = await analyze_intent(question, history, catalog, state.llm, state.settings)
    if not plan.answerable:
        return _empty_response(plan.reason or "This question cannot be answered from the available data.", plan.model_dump())
    ctx = AgentContext(llm=state.llm, settings=state.settings, catalog=catalog, state=state, question=question)
    results = await execute_plan(plan, AGENTS, ctx, state.settings.step_timeout_s)
    response = await aggregate(question, plan, results, ctx)
    response["answerable"] = True
    return response
