from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.main import create_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
