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


async def has_live_code(session: AsyncSession, user: User) -> bool:
    record = await session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    return record is not None and _now() <= record.expires_at and record.attempts < MAX_ATTEMPTS


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
