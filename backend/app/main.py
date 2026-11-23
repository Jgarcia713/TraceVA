"""FastAPI application factory.

Run locally (from backend/, after `alembic upgrade head`), single process only:
    uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.agent.model_client import LLMClient, OpenAICompatClient
from app.agent.replay import RecordedRun, ReplayLLMClient, load_catalog
from app.agent.runner import RunManager, reconcile_interrupted_runs
from app.api import conversations, datasets, demo, health, runs
from app.api.errors import install_error_handling
from app.config import Settings, get_settings
from app.db.session import create_app_engine, create_session_factory

API_PREFIX = "/api/v1"
logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    llm_factory: Callable[[], LLMClient] | None = None,
) -> FastAPI:
    """`llm_factory` builds one model client per run (tests inject a fake)."""
    settings = settings or get_settings()  # validates configuration at startup
    resolved: Settings = settings
    engine = create_app_engine(resolved.database_url)
    session_factory = create_session_factory(engine)
    catalog: dict[str, RecordedRun] = {}
    if llm_factory is not None:
        make_llm = llm_factory
    elif resolved.llm_provider == "replay":
        catalog = load_catalog(resolved.demo_catalog_path)
        delay = resolved.demo_step_delay_s

        def make_llm() -> LLMClient:
            return ReplayLLMClient(catalog, step_delay_s=delay)

    else:

        def make_llm() -> LLMClient:
            return OpenAICompatClient(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Runs left 'running' by a previous process can never finish: mark them failed.
        # The runs table only exists once migrations have been applied.
        try:
            reconcile_interrupted_runs(session_factory)
        except OperationalError as exc:
            if "no such table" not in str(exc):  # unmigrated DB is reported by /health
                logger.exception("startup reconciliation failed")
                app.state.startup_errors.append(f"reconcile: {exc}")
        except Exception as exc:
            logger.exception("startup reconciliation failed")
            app.state.startup_errors.append(f"reconcile: {exc}")
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
    app.state.startup_errors = []
    app.state.demo_catalog = catalog

    install_error_handling(app)
    for module in (health, datasets, conversations, runs, demo):
        app.include_router(module.router, prefix=API_PREFIX)
    return app
