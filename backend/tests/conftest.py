from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.datasets import clear_cache
from app.config import BACKEND_DIR, Settings
from app.db.session import create_app_engine, create_session_factory
from app.main import create_app
from tests.reference import FIXTURES_DIR


@pytest.fixture(autouse=True)
def _fresh_dataframe_cache() -> Iterator[None]:
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def migrated_db(alembic_config: Config, database_url: str) -> str:
    command.upgrade(alembic_config, "head")
    return database_url


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(database_url=database_url, data_dir=FIXTURES_DIR)


@pytest.fixture
def client(settings: Settings, migrated_db: str) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def session(migrated_db: str) -> Iterator[Session]:
    engine = create_app_engine(migrated_db)
    try:
        with create_session_factory(engine)() as db_session:
            yield db_session
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(migrated_db: str) -> Iterator[sessionmaker[Session]]:
    engine = create_app_engine(migrated_db)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def sample_ids(session_factory: sessionmaker[Session]) -> dict[str, str]:
    """Register the 50-row Adult fixture; return its source and root view IDs."""
    from app.analysis.registry import register_dataset, root_view_id

    with session_factory.begin() as db:
        source = register_dataset(db, "adult_sample", FIXTURES_DIR)
        return {"source_id": source.id, "view_id": root_view_id(db, source.id)}
