import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chat, ChatTurn

log = logging.getLogger(__name__)

TITLE_MAX = 120
CONTEXT_TURNS = 3
CONTEXT_ANSWER_MAX = 4000


def make_title(question: str) -> str:
    return " ".join(question.split())[:TITLE_MAX] or "New chat"


async def create_chat(sessionmaker, user_id: int, question: str) -> int:
    async with sessionmaker() as s:
        chat = Chat(user_id=user_id, title=make_title(question))
        s.add(chat)
        await s.commit()
        return chat.id


async def get_owned_chat(session: AsyncSession, chat_id: int, user_id: int) -> Chat | None:
    return await session.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))


async def load_context(sessionmaker, chat_id: int, user_id: int) -> list[dict]:
    """Last answerable turns as planner history, oldest first."""
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(ChatTurn.question, ChatTurn.response)
                .join(Chat, Chat.id == ChatTurn.chat_id)
                .where(ChatTurn.chat_id == chat_id, Chat.user_id == user_id, ChatTurn.response.is_not(None))
                .order_by(ChatTurn.turn_index.desc())
                .limit(20)
            )
        ).all()
    answerable = [(q, r) for q, r in rows if r.get("answerable")]
    latest = answerable[:CONTEXT_TURNS]
    return [{"question": q, "answer": str(r.get("answer", ""))[:CONTEXT_ANSWER_MAX]} for q, r in reversed(latest)]


async def save_turn(
    sessionmaker,
    chat_id: int,
    user_id: int,
    *,
    question: str,
    resp: dict | None,
    status: str,
    error: str | None,
    latency_ms: int,
    cache_hit: bool,
) -> int | None:
    """Persist one turn. Never raises: returns the turn id, or None if not owned / saving failed."""
    try:
        async with sessionmaker() as s:
            chat = await get_owned_chat(s, chat_id, user_id)
            if chat is None:
                return None
            next_index = await s.scalar(select(func.coalesce(func.max(ChatTurn.turn_index), -1) + 1).where(ChatTurn.chat_id == chat_id))
            turn = ChatTurn(
                chat_id=chat_id,
                turn_index=next_index,
                question=question[:2000],
                response=resp,
                status=status,
                error=error,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
            )
            s.add(turn)
            chat.updated_at = func.now()
            await s.commit()
            return turn.id
    except Exception:  # noqa: BLE001 - persistence must never break a query
        log.exception("Failed to save chat turn")
        return None
