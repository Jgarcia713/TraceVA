"""Application settings, loaded from environment variables / `.env` and validated at startup."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'traceva.db').as_posix()}"
    data_dir: Path = REPO_DIR / "datasets"
    max_tool_result_rows: int = Field(default=30, ge=1, le=500)

    @field_validator("database_url")
    @classmethod
    def _require_sqlite(cls, value: str) -> str:
        # FK/transaction handling in app.db.session is SQLite-specific (ADR-002).
        prefix = "sqlite:///"
        if not value.startswith(prefix):
            raise ValueError("only sqlite:/// database URLs are supported")
        # Resolve relative file paths against backend/ (like DATA_DIR), not the CWD.
        path = Path(value.removeprefix(prefix))
        if str(path) != ":memory:" and not path.is_absolute():
            value = f"{prefix}{(BACKEND_DIR / path).resolve().as_posix()}"
        return value

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        path = value if value.is_absolute() else (BACKEND_DIR / value)
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(f"DATA_DIR does not exist or is not a directory: {path}")
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
