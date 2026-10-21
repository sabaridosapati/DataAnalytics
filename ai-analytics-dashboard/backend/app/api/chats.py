from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_session
from app.api.errors import api_error
from app.chats import get_owned_chat
from app.db.models import Chat, ChatTurn, User

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _not_found():
    return api_error(404, "not_found", "Chat not found.")


@router.get("")
async def list_chats(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    turn_count = select(func.count(ChatTurn.id)).where(ChatTurn.chat_id == Chat.id).correlate(Chat).scalar_subquery()
    rows = (
        await session.execute(
            select(Chat, turn_count)
            .where(Chat.user_id == user.id)
            .order_by(Chat.updated_at.desc(), Chat.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(Chat).where(Chat.user_id == user.id))
    items = [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat(), "turn_count": n} for c, n in rows]
    return {"items": items, "total": total or 0}


@router.get("/{chat_id}")
async def get_chat(chat_id: int = Path(..., ge=1, le=2147483647), user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    chat = await get_owned_chat(session, chat_id, user.id)
    if chat is None:
        raise _not_found()
    turns = (await session.scalars(select(ChatTurn).where(ChatTurn.chat_id == chat.id).order_by(ChatTurn.turn_index))).all()
    return {
        "id": chat.id,
        "title": chat.title,
        "created_at": chat.created_at.isoformat(),
        "turns": [
            {
                "id": t.id,
                "turn_index": t.turn_index,
                "question": t.question,
                "response": {**t.response, "cache_hit": t.cache_hit} if t.response is not None else None,
                "status": t.status,
                "error": t.error,
                "created_at": t.created_at.isoformat(),
            }
            for t in turns
        ],
    }


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(chat_id: int = Path(..., ge=1, le=2147483647), user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    chat = await get_owned_chat(session, chat_id, user.id)
    if chat is None:
        raise _not_found()
    await session.delete(chat)
    await session.commit()
