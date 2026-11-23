"""FastAPI application factory.

Run locally (from backend/, after `alembic upgrade head`):
    uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.api.errors import install_error_handling
from app.config import Settings, get_settings
from app.db.session import create_app_engine, create_session_factory

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()  # validates configuration at startup
    engine = create_app_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="TraceVA backend", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    install_error_handling(app)
    app.include_router(health.router, prefix=API_PREFIX)
    return app
