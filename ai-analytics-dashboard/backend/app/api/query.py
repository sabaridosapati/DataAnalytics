import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.agents.intent import PlanValidationError
from app.agents.orchestrator import answer_question
from app.api.deps import current_user
from app.api.errors import api_error
from app.api.ratelimit import limiter
from app.cache.redis_cache import answer_key
from app.chats import create_chat, get_owned_chat, load_context, save_turn
from app.db.models import QueryAudit, User
from app.db.versions import get_catalog_version
from app.llm.provider import LLMError, LLMQuotaError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/query", tags=["query"])


class HistoryTurn(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=4000)


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)
    chat_id: int | None = Field(None, ge=1, le=2147483647)


async def _audit(state, user_id: int, question: str, resp: dict | None, status: str, error: str | None, latency_ms: int, cache_hit: bool) -> None:
    try:
        async with state.sessionmaker() as s:
            s.add(
                QueryAudit(
                    user_id=user_id,
                    question=question[:2000],
                    plan_json={"plan": resp.get("plan"), "steps": resp.get("steps")} if resp else None,
                    sql_executed=resp.get("sql", []) if resp else [],
                    agents_used=[st["agent"] for st in resp.get("steps", [])] if resp else [],
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                    status=status,
                    error=error,
                )
            )
            await s.commit()
    except Exception:  # noqa: BLE001 - auditing must never break a query
        log.exception("Failed to write query audit")


def _cacheable(resp: dict) -> bool:
    return resp.get("answerable") and not resp.get("unverified_numbers") and all(s["status"] == "ok" for s in resp.get("steps", []))


@router.post("")
@limiter.limit("30/minute")
async def query(request: Request, body: QueryIn, user: User = Depends(current_user)):
    state = request.app.state
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    question = " ".join(body.question.split())
    if not question:
        raise api_error(422, "validation_error", "question: must not be blank")
    chat_id = body.chat_id
    if chat_id is not None:
        async with state.sessionmaker() as s:
            if await get_owned_chat(s, chat_id, user.id) is None:
                raise api_error(404, "not_found", "Chat not found.")
        history = await load_context(state.sessionmaker, chat_id, user.id)
    else:
        history = [h.model_dump() for h in body.history[-3:]]
        chat_id = await create_chat(state.sessionmaker, user.id, question)

    def fail(status_code: int, code: str, message: str):
        return api_error(status_code, code, message, chat_id=chat_id)

    async with state.sessionmaker() as s:
        version = await get_catalog_version(s)
    key = answer_key(version, question) if not history else None

    resp, status, error, cache_hit, turn_id = None, "ok", None, False, None
    try:
        if key and (cached := await state.cache.get_json(key)) is not None:
            resp, cache_hit = cached, True
        else:
            # Overall deadline below nginx's 180 s proxy timeout, so clients always get a JSON error
            resp = await asyncio.wait_for(answer_question(state, question, history), timeout=state.settings.query_timeout_s)
            if not resp.get("answerable"):
                status = "unanswerable"
            elif any(s["status"] != "ok" for s in resp["steps"]):
                status = "partial"
            if key and _cacheable(resp):
                await state.cache.set_json(key, resp, state.settings.cache_ttl_s)
    except PlanValidationError as exc:
        status, error = "plan_failed", str(exc)
        raise fail(422, "plan_failed", "I couldn't turn this question into a valid analysis plan. Try rephrasing it.")
    except LLMQuotaError as exc:
        status, error = "llm_quota_exhausted", str(exc)
        raise fail(503, status, "The AI provider's API credits or quota are exhausted. Ask the administrator to check AI provider API billing and project limits.")
    except LLMError as exc:
        status, error = "llm_unavailable", str(exc)
        raise fail(502, "llm_unavailable", "The language model is unavailable right now. Please try again shortly.")
    except asyncio.TimeoutError:
        status, error = "timeout", f"exceeded {state.settings.query_timeout_s}s"
        raise fail(504, "query_timeout", "This question took too long to answer. Try a narrower question or retry shortly.")
    except Exception as exc:
        status, error = "error", repr(exc)
        log.exception("Unexpected query failure")
        raise fail(500, "internal_error", "An unexpected error occurred.") from exc
    finally:
        latency = int((time.perf_counter() - started) * 1000)
        await _audit(state, user.id, question, resp, status, error, latency, cache_hit)
        turn_id = await save_turn(
            state.sessionmaker, chat_id, user.id, question=question, resp=resp, status=status, error=error, latency_ms=latency, cache_hit=cache_hit
        )

    return {**resp, "cache_hit": cache_hit, "request_id": request_id, "chat_id": chat_id, "turn_id": turn_id}
