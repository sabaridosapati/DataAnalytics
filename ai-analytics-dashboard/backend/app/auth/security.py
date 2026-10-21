import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def password_problem(password: str) -> str | None:
    """Return a human-readable problem with the password, or None if acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password.encode("utf-8")) > 72:
        return "Password must be at most 72 bytes."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain at least one letter and one digit."
    return None


def create_access_token(user_id: int, role: str, secret: str, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"])


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str, user_id: int, secret: str) -> str:
    return hmac.new(secret.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()
