from __future__ import annotations

from typing import Any

import pytest

from app.agent import tool_registry
from app.agent.tool_registry import execute, model_tool_schemas, tool_schema_hash
from app.analysis.tools import ToolContext


@pytest.fixture
def ctx() -> ToolContext:
    # Validation failures return before any DB/file access, so no session is needed.
    return ToolContext(session=None, data_dir=None, max_result_rows=30)  # type: ignore[arg-type]


def test_schemas_expose_tools_and_submit_answer() -> None:
    schemas = model_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert names == ["inspect_dataset", "create_view", "summarize", "submit_answer"]
    for schema in schemas:
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert params.get("additionalProperties") is False  # strict inputs


def test_schema_hash_is_stable_and_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    original = tool_schema_hash()
    assert tool_schema_hash() == original
    monkeypatch.setattr(tool_registry, "SUBMIT_ANSWER_DESCRIPTION", "changed")
    assert tool_schema_hash() != original


@pytest.mark.parametrize(
    ("name", "raw", "code"),
    [
        ("drop_tables", "{}", "UNKNOWN_TOOL"),
        ("summarize", "{oops", "INVALID_JSON"),
        ("summarize", "[1, 2]", "INVALID_JSON"),
        ("summarize", '{"view_id": "v"}', "INVALID_ARGUMENTS"),
        (
            "summarize",
            '{"view_id": "v", "columns": ["a"], "metrics": ["mean"], "x": 1}',
            "INVALID_ARGUMENTS",
        ),
    ],
)
def test_execute_rejects_bad_calls(ctx: ToolContext, name: str, raw: str, code: str) -> None:
    outcome = execute(ctx, name, raw)
    assert not outcome.ok and outcome.result is None and outcome.args is None
    assert outcome.error is not None and outcome.error["code"] == code


def test_validation_message_names_the_field(ctx: ToolContext) -> None:
    outcome = execute(ctx, "summarize", '{"view_id": "v", "metrics": ["mean"]}')
    error: Any = outcome.error
    assert "columns" in error["message"]
