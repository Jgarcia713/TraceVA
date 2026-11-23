"""Registry of model-callable tools: JSON Schema export and defensive execution.

`execute` never raises for model mistakes. It returns a `ToolOutcome` that is either a
success (validated args + typed result) or a failure (machine-readable error), so the
orchestrator can record every attempt and feed errors back for repair.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.analysis.tools import ToolContext, inspect_dataset, summarize
from app.schemas.agent import SubmitAnswerInput
from app.schemas.tools import (
    InspectDatasetInput,
    SummarizeInput,
    ToolError,
    ToolErrorCode,
)

logger = logging.getLogger(__name__)

SUBMIT_ANSWER = "submit_answer"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[ToolContext, Any], BaseModel]
    input_ids: Callable[[Any], list[str]]


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            name="inspect_dataset",
            description=(
                "Profile a dataset source: row count, column kinds, missing values, "
                "numeric ranges, and most frequent categories."
            ),
            input_model=InspectDatasetInput,
            run=inspect_dataset,
            input_ids=lambda a: [a.source_id],
        ),
        ToolSpec(
            name="summarize",
            description=(
                "Descriptive statistics for columns of a view, optionally grouped. "
                "Numeric metrics: mean, median, std, min, max. Categorical: proportions."
            ),
            input_model=SummarizeInput,
            run=summarize,
            input_ids=lambda a: [a.view_id],
        ),
    ]
}

SUBMIT_ANSWER_DESCRIPTION = (
    "Finish the run with your answer. Call exactly once, after gathering evidence. "
    "Every claim must cite evidence_id values returned by tools in this run."
)


def _function_schema(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


def model_tool_schemas() -> list[dict[str, Any]]:
    """Tool list sent to the model: analytical tools plus the terminal submit_answer."""
    schemas = [_function_schema(s.name, s.description, s.input_model) for s in TOOLS.values()]
    schemas.append(_function_schema(SUBMIT_ANSWER, SUBMIT_ANSWER_DESCRIPTION, SubmitAnswerInput))
    return schemas


def tool_schema_hash() -> str:
    """Hash of everything the model sees about tools. Field descriptions are part of the
    effective prompt, so this goes into run metadata alongside PROMPT_VERSION."""
    canonical = json.dumps(model_tool_schemas(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def summarize_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


@dataclass(frozen=True)
class ToolOutcome:
    args: dict[str, Any] | None  # validated arguments (JSON-safe), None if invalid
    input_ids: list[str]
    result: dict[str, Any] | None  # JSON-safe full result on success
    error: dict[str, Any] | None  # {code, message, details} on failure

    @property
    def ok(self) -> bool:
        return self.error is None


def _failure(
    code: ToolErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
    args: dict[str, Any] | None = None,
    input_ids: list[str] | None = None,
) -> ToolOutcome:
    return ToolOutcome(
        args=args,
        input_ids=input_ids or [],
        result=None,
        error={"code": str(code), "message": message, "details": details or {}},
    )


def parse_arguments(raw_arguments: str) -> dict[str, Any] | ToolOutcome:
    try:
        parsed = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return _failure(ToolErrorCode.INVALID_JSON, f"arguments are not valid JSON: {exc.msg}")
    if not isinstance(parsed, dict):
        return _failure(ToolErrorCode.INVALID_JSON, "arguments must be a JSON object")
    return parsed


def execute(ctx: ToolContext, name: str, raw_arguments: str) -> ToolOutcome:
    spec = TOOLS.get(name)
    if spec is None:
        return _failure(
            ToolErrorCode.UNKNOWN_TOOL,
            f"unknown tool: {name!r}",
            {"available": [*TOOLS, SUBMIT_ANSWER]},
        )
    parsed = parse_arguments(raw_arguments)
    if isinstance(parsed, ToolOutcome):
        return parsed
    try:
        args = spec.input_model.model_validate(parsed)
    except ValidationError as exc:
        return _failure(ToolErrorCode.INVALID_ARGUMENTS, summarize_validation_error(exc))

    args_json = args.model_dump(mode="json")
    input_ids = spec.input_ids(args)
    try:
        result = spec.run(ctx, args)
    except ToolError as exc:
        return _failure(exc.code, exc.message, exc.details, args=args_json, input_ids=input_ids)
    except Exception:  # a bug in a tool must not crash the run or vanish silently
        logger.exception("tool %s raised unexpectedly", name)
        return _failure(
            ToolErrorCode.INTERNAL_ERROR,
            "the tool failed unexpectedly",
            args=args_json,
            input_ids=input_ids,
        )
    return ToolOutcome(
        args=args_json, input_ids=input_ids, result=result.model_dump(mode="json"), error=None
    )
