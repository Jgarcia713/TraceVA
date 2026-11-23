from __future__ import annotations

import pytest

from app.analysis.registry import registrable_datasets
from app.config import Settings
from tests.reference import FIXTURES_DIR


def test_context_guard_can_be_disabled_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_CONTEXT_TOKENS", "none")
    monkeypatch.setenv("LLM_SEED", "none")
    settings = Settings(data_dir=FIXTURES_DIR)
    assert settings.llm_context_tokens is None and settings.llm_seed is None


def test_secret_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-leak")
    assert "sk-should-not-leak" not in repr(Settings(data_dir=FIXTURES_DIR))


def test_registration_script_excludes_test_fixtures() -> None:
    assert registrable_datasets() == ["adult"]
