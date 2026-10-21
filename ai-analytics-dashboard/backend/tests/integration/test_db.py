import asyncpg
import pytest
from sqlalchemy import select, text

from app.auth.security import verify_password
from app.config import get_settings
from app.db.models import User
from app.db.seed import seed_admin
from app.db.versions import bump_catalog_version, get_catalog_version


async def test_tables_created(db):
    async with db.engine.connect() as c:
        rows = await c.execute(
            text(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema IN ('app', 'vector')"
            )
        )
        names = set(rows.scalars())
    expected = {
        "app.users", "app.email_codes", "app.datasets", "app.dataset_columns",
        "app.query_audit", "app.dashboard_pins", "app.catalog_version", "vector.chunks",
    }
    assert expected <= names


async def test_seed_admin_idempotent(db):
    settings = get_settings()
    await seed_admin(db.sessionmaker, settings)
    await seed_admin(db.sessionmaker, settings)
    async with db.sessionmaker() as s:
        admins = (await s.scalars(select(User).where(User.username == "admin"))).all()
    assert len(admins) == 1
    admin = admins[0]
    assert admin.role == "admin" and admin.is_verified and admin.is_active
    assert admin.email == "admin@analytics.local"
    assert verify_password("Test@123", admin.password_hash)


async def test_catalog_version_bump(db):
    async with db.sessionmaker() as s:
        v1 = await get_catalog_version(s)
        await bump_catalog_version(s)
        await s.commit()
        assert await get_catalog_version(s) == v1 + 1


async def test_query_ro_role_is_locked_down(db):
    conn = await asyncpg.connect(get_settings().query_ro_url)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM app.users")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT * FROM vector.chunks")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("CREATE TABLE data.evil (id int)")
    finally:
        await conn.close()
