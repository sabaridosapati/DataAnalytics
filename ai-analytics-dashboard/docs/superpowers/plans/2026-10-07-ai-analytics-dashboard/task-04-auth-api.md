# Task 4: App factory + auth API (register / verify code / login)

**Files:**
- Create: `backend/app/api/__init__.py` (empty), `backend/app/api/errors.py`, `backend/app/api/ratelimit.py`, `backend/app/api/deps.py`, `backend/app/api/auth.py`
- Create: `backend/app/auth/mailer.py`, `backend/app/auth/email_codes.py`
- Replace: `backend/app/main.py`
- Test: `backend/tests/integration/test_auth.py`

**Interfaces:**
- Consumes: Task 2 models/session/seed/security; Task 3 `build_provider`, `RedisCache`.
- Produces `app.api.errors.api_error(status, code, message) -> HTTPException`; every error response body is `{"error": {"code", "message"}}`.
- Produces `app.api.ratelimit.limiter` (slowapi `Limiter`; key = JWT subject if valid bearer token, else client IP).
- Produces `app.api.deps`: `get_session` (yields `AsyncSession`), `current_user` (→ `User`, 401 if missing/invalid/unverified/inactive), `admin_user` (403 unless admin).
- Produces `app.main.create_app() -> FastAPI` and module-level `app`. `app.state` holds: `settings`, `engine`, `sessionmaker`, `ro_pool` (asyncpg pool as `query_ro`), `cache` (`RedisCache`), `llm`, `catalog_cache` (None initially), `ingest_lock` (`asyncio.Lock`).
- Produces endpoints: `POST /api/auth/register` (201), `POST /api/auth/verify` (200, `TokenOut`), `POST /api/auth/resend-code`, `POST /api/auth/login` (`{identifier, password}` → `TokenOut`), `GET /api/auth/me`, `GET /api/health`.
- `TokenOut = {access_token, token_type: "bearer", user: {id, email, username, role}}`.

- [ ] **Step 1: Write the failing auth integration tests**

**File: `backend/tests/integration/test_auth.py`**
```python
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db.models import EmailCode, User
from helpers import auth, mailpit_code, register_verified_user, unique_email


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] and body["redis"]


async def test_admin_login_with_username_and_email(client):
    for identifier in ("admin", "admin@analytics.local", "ADMIN@analytics.local"):
        r = await client.post("/api/auth/login", json={"identifier": identifier, "password": "Test@123"})
        assert r.status_code == 200, (identifier, r.text)
        body = r.json()
        assert body["user"]["role"] == "admin" and body["token_type"] == "bearer"
    me = await client.get("/api/auth/me", headers=auth(body["access_token"]))
    assert me.json()["username"] == "admin"


async def test_wrong_password(client):
    r = await client.post("/api/auth/login", json={"identifier": "admin", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


async def test_register_verify_login_flow(client):
    email, token = await register_verified_user(client)
    me = await client.get("/api/auth/me", headers=auth(token))
    assert me.status_code == 200 and me.json()["email"] == email and me.json()["role"] == "user"
    r = await client.post("/api/auth/login", json={"identifier": email.upper(), "password": "Passw0rd!"})
    assert r.status_code == 200


async def test_unverified_user_cannot_login(client):
    email = unique_email()
    await client.post("/api/auth/register", json={"email": email, "password": "Passw0rd!"})
    r = await client.post("/api/auth/login", json={"identifier": email, "password": "Passw0rd!"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "unverified"


async def test_weak_password_and_bad_email(client):
    r = await client.post("/api/auth/register", json={"email": unique_email(), "password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"
    r = await client.post("/api/auth/register", json={"email": "not-an-email", "password": "Passw0rd!"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_duplicate_verified_email(client):
    email, _ = await register_verified_user(client)
    r = await client.post("/api/auth/register", json={"email": email, "password": "Passw0rd!"})
    assert r.status_code == 409


async def test_wrong_code_attempts_lock(client):
    email = unique_email()
    await client.post("/api/auth/register", json={"email": email, "password": "Passw0rd!"})
    code = await mailpit_code(email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        r = await client.post("/api/auth/verify", json={"email": email, "code": wrong})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"
    r = await client.post("/api/auth/verify", json={"email": email, "code": wrong})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"
    r = await client.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 429


async def test_expired_code(client, db):
    email = unique_email()
    await client.post("/api/auth/register", json={"email": email, "password": "Passw0rd!"})
    code = await mailpit_code(email)
    async with db.sessionmaker() as s:
        user = await s.scalar(select(User).where(User.email == email))
        await s.execute(
            update(EmailCode)
            .where(EmailCode.user_id == user.id)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await s.commit()
    r = await client.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 400 and r.json()["error"]["code"] == "code_expired"


async def test_resend_cooldown_then_new_code(client, db):
    email = unique_email()
    await client.post("/api/auth/register", json={"email": email, "password": "Passw0rd!"})
    r = await client.post("/api/auth/resend-code", json={"email": email})
    assert r.status_code == 429 and r.json()["error"]["code"] == "cooldown"
    async with db.sessionmaker() as s:
        user = await s.scalar(select(User).where(User.email == email))
        await s.execute(
            update(EmailCode)
            .where(EmailCode.user_id == user.id)
            .values(last_sent_at=datetime.now(timezone.utc) - timedelta(minutes=2))
        )
        await s.commit()
    r = await client.post("/api/auth/resend-code", json={"email": email})
    assert r.status_code == 200
    code = await mailpit_code(email)
    r = await client.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200


async def test_resend_unknown_email_does_not_leak(client):
    r = await client.post("/api/auth/resend-code", json={"email": unique_email()})
    assert r.status_code == 200


async def test_me_requires_valid_token(client):
    assert (await client.get("/api/auth/me")).status_code == 401
    r = await client.get("/api/auth/me", headers=auth("garbage"))
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


async def test_deactivated_user_token_rejected(client, db):
    email, token = await register_verified_user(client)
    async with db.sessionmaker() as s:
        await s.execute(update(User).where(User.email == email).values(is_active=False))
        await s.commit()
    assert (await client.get("/api/auth/me", headers=auth(token))).status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_auth.py -q`
Expected: FAIL — `ImportError: cannot import name 'create_app' from 'app.main'`.

- [ ] **Step 3: Implement error handling, rate limiting, deps**

**File: `backend/app/api/__init__.py`**
```python
```

**File: `backend/app/api/errors.py`**
```python
import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        detail = exc.detail
    else:
        default_codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        detail = {"code": default_codes.get(exc.status_code, "error"), "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=getattr(exc, "headers", None))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
    msg = first.get("msg", "Invalid input")
    message = f"{loc}: {msg}" if loc else msg
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": message}})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500, content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
    )
```

**File: `backend/app/api/ratelimit.py`**
```python
import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _rate_key(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        try:
            payload = jwt.decode(header[7:], request.app.state.settings.jwt_secret, algorithms=["HS256"])
            return f"user:{payload['sub']}"
        except Exception:  # noqa: BLE001 - fall back to IP for bad tokens
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_key)


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429, content={"error": {"code": "rate_limited", "message": f"Too many requests ({exc.detail})."}}
    )
```

**File: `backend/app/api/deps.py`**
```python
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
```

- [ ] **Step 4: Implement mailer and email codes**

**File: `backend/app/auth/mailer.py`**
```python
from email.message import EmailMessage

import aiosmtplib


class MailError(Exception):
    pass


async def send_email(settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        raise MailError(str(exc)) from exc
```

**File: `backend/app/auth/email_codes.py`**
```python
import hmac
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.mailer import send_email
from app.auth.security import generate_code, hash_code
from app.db.models import EmailCode, User

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = timedelta(seconds=60)


class CooldownError(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


class VerifyResult(Enum):
    OK = "ok"
    INVALID = "invalid"
    EXPIRED = "expired"
    TOO_MANY = "too_many"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def issue_code(session: AsyncSession, user: User, settings) -> None:
    """Create/replace the user's code and email it. Commits only if the email was sent."""
    now = _now()
    record = await session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    if record is not None and now - record.last_sent_at < RESEND_COOLDOWN:
        raise CooldownError(int((RESEND_COOLDOWN - (now - record.last_sent_at)).total_seconds()) + 1)
    code = generate_code()
    if record is None:
        record = EmailCode(user_id=user.id)
        session.add(record)
    record.code_hash = hash_code(code, user.id, settings.jwt_secret)
    record.expires_at = now + CODE_TTL
    record.attempts = 0
    record.last_sent_at = now
    await session.flush()
    try:
        await send_email(
            settings,
            user.email,
            "Your AI Analytics Dashboard verification code",
            f"Your verification code is {code}\n\nIt expires in 10 minutes. "
            "If you did not request this, you can ignore this email.",
        )
    except Exception:
        await session.rollback()
        raise
    await session.commit()


async def check_code(session: AsyncSession, user: User, code: str, settings) -> VerifyResult:
    record = await session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    if record is None:
        return VerifyResult.INVALID
    if record.attempts >= MAX_ATTEMPTS:
        return VerifyResult.TOO_MANY
    if _now() > record.expires_at:
        return VerifyResult.EXPIRED
    if not hmac.compare_digest(record.code_hash, hash_code(code, user.id, settings.jwt_secret)):
        record.attempts += 1
        await session.commit()
        return VerifyResult.TOO_MANY if record.attempts >= MAX_ATTEMPTS else VerifyResult.INVALID
    await session.delete(record)
    user.is_verified = True
    await session.commit()
    return VerifyResult.OK
```

- [ ] **Step 5: Implement the auth router**

**File: `backend/app/api/auth.py`**
```python
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_session
from app.api.errors import api_error
from app.api.ratelimit import limiter
from app.auth.email_codes import CooldownError, VerifyResult, check_code, issue_code
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
    else:
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
```

- [ ] **Step 6: Replace main.py with the app factory**

**File: `backend/app/main.py`**
```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth
from app.api.errors import http_exception_handler, unhandled_exception_handler, validation_exception_handler
from app.api.ratelimit import limiter, rate_limit_handler
from app.cache.redis_cache import RedisCache
from app.config import get_settings
from app.db.seed import seed_admin
from app.db.session import create_engine_and_sessionmaker, init_db, wait_for_db
from app.llm.provider import build_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROUTERS = [auth.router]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    app.state.llm = build_provider(settings)  # fail fast on a missing API key
    engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    await wait_for_db(engine)
    await init_db(engine)
    await seed_admin(sessionmaker, settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.ro_pool = await asyncpg.create_pool(settings.query_ro_url, min_size=1, max_size=5)
    app.state.cache = RedisCache(settings.redis_url)
    app.state.catalog_cache = None
    app.state.ingest_lock = asyncio.Lock()
    os.makedirs(settings.upload_dir, exist_ok=True)
    try:
        yield
    finally:
        await app.state.ro_pool.close()
        await app.state.cache.close()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Analytics Dashboard", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    limiter.enabled = settings.rate_limit_enabled
    app.state.limiter = limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    for router in ROUTERS:
        app.include_router(router)

    @app.get("/api/health")
    async def health():
        db_ok = False
        try:
            async with app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # noqa: BLE001
            pass
        redis_ok = await app.state.cache.ping()
        ok = db_ok and redis_ok
        body = {"status": "ok" if ok else "degraded", "database": db_ok, "redis": redis_ok, "llm_provider": settings.llm_provider}
        return JSONResponse(status_code=200 if ok else 503, content=body)

    return app


app = create_app()
```

- [ ] **Step 7: Run the auth tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_auth.py -q`
Expected: all pass.

- [ ] **Step 8: Run all tests so far**

Run: `docker compose run --rm backend pytest -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend
git commit -m "feat(auth): app factory, email-code registration, login, JWT, rate limiting"
```
