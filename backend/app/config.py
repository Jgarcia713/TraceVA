"""Application settings, loaded from environment variables / `.env` and validated at startup."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", extra="ignore", env_parse_none_str="none"
    )

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'traceva.db').as_posix()}"
    data_dir: Path = REPO_DIR / "datasets"
    max_tool_result_rows: int = Field(default=30, ge=1, le=500)

    # --- LLM (Milestone 2). Provider-agnostic OpenAI-compatible transport. ---
    llm_provider: str = "ollama"  # label recorded in run metadata
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "traceva-qwen3.5-4b-8k"  # see ollama/*.Modelfile
    llm_api_key: SecretStr = SecretStr("ollama")  # never logged or persisted
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_thinking: bool = False  # off: sends reasoning_effort="none"
    llm_seed: int | None = None
    llm_timeout_s: float = Field(default=300.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)  # transport-level only
    # Server-side context window (must match the model's num_ctx). Ollama silently drops
    # the oldest context when exceeded, so the orchestrator fails the run instead.
    # None disables the guard (e.g. hosted models with very large windows).
    llm_context_tokens: int | None = Field(default=8192, ge=1024)
    llm_output_reserve_tokens: int = Field(default=1024, ge=128)

    # --- Agent budgets ---
    # 8: a filter question needs >= 3 steps (create_view, summarize, submit_answer);
    # 6 left too little room for repairs (docs/implementation/create-view.md).
    max_agent_steps: int = Field(default=8, ge=1, le=20)
    max_run_seconds: float = Field(default=900.0, gt=0)
    max_tool_calls_per_step: int = Field(default=4, ge=1, le=10)
    max_tool_result_chars: int = Field(default=6000, ge=500, le=50_000)
    # Background runs executed in parallel by the API (others wait, shown as 'running').
    max_concurrent_runs: int = Field(default=2, ge=1, le=8)

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
