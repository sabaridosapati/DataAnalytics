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
