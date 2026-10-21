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
