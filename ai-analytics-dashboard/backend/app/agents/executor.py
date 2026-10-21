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
