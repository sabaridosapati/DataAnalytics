from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_session
from app.api.errors import api_error
from app.api.ratelimit import limiter
from app.auth.email_codes import CooldownError, VerifyResult, check_code, has_live_code, issue_code
from app.auth.mailer import MailError
from app.auth.security import create_access_token, hash_password, password_problem, verify_password
from app.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class VerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class EmailIn(BaseModel):
    email: EmailStr


class LoginIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    username: str | None
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MessageOut(BaseModel):
    message: str


def _token(user: User, settings) -> TokenOut:
    token = create_access_token(user.id, user.role, settings.jwt_secret, settings.jwt_expire_minutes)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/register", status_code=201, response_model=MessageOut)
@limiter.limit("10/minute")
async def register(request: Request, body: RegisterIn, session: AsyncSession = Depends(get_session)):
    settings = request.app.state.settings
    problem = password_problem(body.password)
    if problem:
        raise api_error(422, "weak_password", problem)
    email = body.email.lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is not None and user.is_verified:
        raise api_error(409, "email_taken", "An account with this email already exists.")
    if user is None:
        user = User(email=email, password_hash=hash_password(body.password), role="user", is_verified=False)
        session.add(user)
    elif not await has_live_code(session, user):
        # Only replace a pending account's password once its code has expired; otherwise
        # anyone could swap the password of an address whose owner is about to verify it.
        user.password_hash = hash_password(body.password)
    await session.commit()
    try:
        await issue_code(session, user, settings)
    except CooldownError:
        pass  # a code was sent less than a minute ago and is still valid
    except MailError:
        raise api_error(503, "email_failed", "Could not send the verification email. Please try again shortly.")
    return MessageOut(message="Verification code sent. Check your email.")


@router.post("/verify", response_model=TokenOut)
@limiter.limit("10/minute")
async def verify(request: Request, body: VerifyIn, session: AsyncSession = Depends(get_session)):
    settings = request.app.state.settings
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        raise api_error(400, "invalid_code", "Invalid code.")
    if user.is_verified:
        raise api_error(400, "already_verified", "This account is already verified. Please sign in.")
    result = await check_code(session, user, body.code, settings)
    if result is VerifyResult.OK:
        return _token(user, settings)
    errors = {
        VerifyResult.INVALID: (400, "invalid_code", "Invalid code."),
        VerifyResult.EXPIRED: (400, "code_expired", "This code has expired. Request a new one."),
        VerifyResult.TOO_MANY: (429, "too_many_attempts", "Too many wrong attempts. Request a new code."),
    }
    raise api_error(*errors[result])


@router.post("/resend-code", response_model=MessageOut)
@limiter.limit("10/minute")
async def resend_code(request: Request, body: EmailIn, session: AsyncSession = Depends(get_session)):
    settings = request.app.state.settings
    generic = MessageOut(message="If the account exists and is not verified yet, a new code has been sent.")
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or user.is_verified:
        return generic
    try:
        await issue_code(session, user, settings)
    except CooldownError as exc:
        raise api_error(429, "cooldown", f"Please wait {exc.retry_after} seconds before requesting a new code.")
    except MailError:
        raise api_error(503, "email_failed", "Could not send the verification email. Please try again shortly.")
    return generic


@router.post("/login", response_model=TokenOut)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginIn, session: AsyncSession = Depends(get_session)):
    settings = request.app.state.settings
    ident = body.identifier.strip()
    user = await session.scalar(select(User).where(or_(User.email == ident.lower(), User.username == ident)))
    if user is None or not verify_password(body.password, user.password_hash):
        raise api_error(401, "invalid_credentials", "Invalid email/username or password.")
    if not user.is_verified:
        raise api_error(403, "unverified", "Please verify your email before signing in.")
    if not user.is_active:
        raise api_error(403, "inactive", "This account has been deactivated.")
    return _token(user, settings)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user
