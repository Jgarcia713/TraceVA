"""FastAPI application factory.

Run locally (from backend/, after `alembic upgrade head`), single process only:
    uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.model_client import LLMClient, OpenAICompatClient
from app.agent.runner import RunManager, reconcile_interrupted_runs
from app.api import conversations, datasets, health, runs
from app.api.errors import install_error_handling
from app.config import Settings, get_settings
from app.db.session import create_app_engine, create_session_factory

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    llm_factory: Callable[[], LLMClient] | None = None,
) -> FastAPI:
    """`llm_factory` builds one model client per run (tests inject a fake)."""
    settings = settings or get_settings()  # validates configuration at startup
    resolved: Settings = settings
    engine = create_app_engine(resolved.database_url)
    session_factory = create_session_factory(engine)
    make_llm = llm_factory or (lambda: OpenAICompatClient(resolved))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Runs left 'running' by a previous process can never finish: mark them failed.
        # The runs table only exists once migrations have been applied.
        try:
            reconcile_interrupted_runs(session_factory)
        except Exception:  # unmigrated database: /health reports it as degraded
            app.state.reconcile_error = True
        manager = RunManager(session_factory, resolved, make_llm)
        app.state.run_manager = manager
        try:
            yield
        finally:
            manager.shutdown(wait=True)
            engine.dispose()

    app = FastAPI(title="TraceVA backend", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.api_prefix = API_PREFIX

    install_error_handling(app)
    for module in (health, datasets, conversations, runs):
        app.include_router(module.router, prefix=API_PREFIX)
    return app
