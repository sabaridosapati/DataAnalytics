import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.security import hash_password
from app.config import Settings
from app.db.models import User

log = logging.getLogger(__name__)


async def seed_admin(sessionmaker: async_sessionmaker, settings: Settings) -> None:
    async with sessionmaker() as session:
        existing = await session.scalar(select(User).where(User.username == "admin"))
        if existing:
            return
        session.add(
            User(
                email=settings.admin_email.lower(),
                username="admin",
                password_hash=hash_password(settings.admin_password),
                role="admin",
                is_verified=True,
                is_active=True,
            )
        )
        await session.commit()
        log.info("Seeded default admin user 'admin'")
