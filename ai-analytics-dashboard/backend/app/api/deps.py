from collections.abc import AsyncIterator

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import api_error
from app.auth.security import decode_access_token
from app.db.models import User

bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if creds is None:
        raise api_error(401, "unauthorized", "Authentication required.")
    try:
        payload = decode_access_token(creds.credentials, request.app.state.settings.jwt_secret)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise api_error(401, "unauthorized", "Invalid or expired token.")
    user = await session.get(User, user_id)
    if user is None or not user.is_active or not user.is_verified:
        raise api_error(401, "unauthorized", "Account is not active.")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise api_error(403, "forbidden", "Administrator access required.")
    return user
