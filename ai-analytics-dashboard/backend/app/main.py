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

from app.api import admin, auth, chats, dashboard, datasets, query
from app.api.errors import http_exception_handler, unhandled_exception_handler, validation_exception_handler
from app.api.ratelimit import limiter, rate_limit_handler
from app.cache.redis_cache import RedisCache
from app.config import get_settings
from app.db.seed import seed_admin
from app.db.session import create_engine_and_sessionmaker, init_db, wait_for_db
from app.duckdb_store import cleanup_stale_tmp
from app.ingestion.pipeline import recover_interrupted
from app.llm.provider import build_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROUTERS = [auth.router, datasets.router, query.router, chats.router, dashboard.router, admin.router]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    app.state.llm = build_provider(settings)  # fail fast on a missing API key
    engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    await wait_for_db(engine)
    await init_db(engine)
    await seed_admin(sessionmaker, settings)
    await recover_interrupted(sessionmaker)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.ro_pool = await asyncpg.create_pool(settings.query_ro_url, min_size=1, max_size=5)
    app.state.cache = RedisCache(settings.redis_url)
    app.state.catalog_cache = None
    app.state.ingest_lock = asyncio.Lock()
    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.duckdb_path, exist_ok=True)
    cleanup_stale_tmp(settings.duckdb_path)
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
