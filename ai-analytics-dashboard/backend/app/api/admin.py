from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_user, get_session
from app.api.errors import api_error
from app.db.models import QueryAudit, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    username: str | None
    role: str
    is_verified: bool
    is_active: bool
    created_at: datetime


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: Literal["admin", "user"] | None = None


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(admin: User = Depends(admin_user), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(User).order_by(User.id))).all()


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(user_id: int, body: UserPatch, admin: User = Depends(admin_user), session: AsyncSession = Depends(get_session)):
    if user_id == admin.id:
        raise api_error(400, "cannot_modify_self", "You cannot change your own account here.")
    user = await session.get(User, user_id)
    if user is None:
        raise api_error(404, "not_found", "User not found.")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.role is not None:
        user.role = body.role
    await session.commit()
    return user


@router.get("/audit")
async def audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    total = await session.scalar(select(func.count()).select_from(QueryAudit))
    rows = (
        await session.execute(
            select(QueryAudit, User.email)
            .outerjoin(User, User.id == QueryAudit.user_id)
            .order_by(QueryAudit.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        {
            "id": a.id,
            "user_email": email,
            "question": a.question,
            "status": a.status,
            "error": a.error,
            "latency_ms": a.latency_ms,
            "cache_hit": a.cache_hit,
            "agents_used": a.agents_used,
            "sql_executed": a.sql_executed,
            "created_at": a.created_at.isoformat(),
        }
        for a, email in rows
    ]
    return {"items": items, "total": total or 0}
