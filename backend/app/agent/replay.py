"""Replay of recorded model responses (demo mode).

A `ReplayLLMClient` returns the model responses of a previously recorded *real* run,
step by step, instead of calling a model. Everything else executes live: argument
validation, tool execution on the real data, evidence, citation checks, assumptions,
the timeline, and the provenance graph.

The recorded responses cite IDs (evidence, views) from the original run. Live
re-execution produces new IDs, so the client maps them by alignment: the i-th tool
result in this run corresponds to the i-th operation of the recorded run. The client
learns new IDs from tool results in the conversation and substitutes them into later
recorded arguments. If live results diverge, the normal validation rules apply (e.g. a
citation that cannot be mapped is rejected), so replay never bypasses a check.

Run metadata records `provider: "replay"` and the source run, so replayed runs are
never confused with model runs.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.model_client import LLMUnavailableError, Message, ModelResponse, ToolCallRequest
from app.analysis.registry import root_view_id
from app.db.models import (
    AnalysisRun,
    ChatMessage,
    DatasetSource,
    Evidence,
    ModelCall,
    Operation,
)

CATALOG_VERSION = 1
# Matches the dataset lines of app.agent.prompts.dataset_context (what the model sees).
_DATASET_LINE = re.compile(r"- (\S+): source_id=(src_[0-9a-f]+), view_id=(view_[0-9a-f]+)")


@dataclass(frozen=True)
class RecordedRun:
    """Model-visible responses and ID trace of one real run (no data, no reasoning)."""

    source_run_id: str
    question: str
    model: str
    prompt_version: str
    steps: list[dict[str, Any]]  # [{content, tool_calls: [{id, name, arguments}], finish_reason}]
    # Per operation, in order: IDs the original run produced (evidence and outputs).
    op_ids: list[dict[str, Any]]  # [{evidence_id, output_ids}]
    recorded: dict[str, Any] = field(default_factory=dict)  # wall_s, model_calls, ...
    # Registered datasets the original run saw: [{name, source_id, root_view_id}]. Mapped
    # by name to the live IDs, so a catalog replays on any database with the same data.
    datasets: list[dict[str, str]] = field(default_factory=list)


class UnknownDemoQuestionError(ValueError):
    """The question is not in the replay catalog (demo mode answers recorded ones only)."""


def load_catalog(path: Path) -> dict[str, RecordedRun]:
    """Catalog keyed by exact question text."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != CATALOG_VERSION:
        raise ValueError(f"unsupported demo catalog version: {data.get('version')}")
    return {r["question"]: RecordedRun(**r) for r in data["runs"]}


class ReplayLLMClient:
    def __init__(self, catalog: dict[str, RecordedRun], step_delay_s: float = 0.0) -> None:
        self._catalog = catalog
        self._delay = step_delay_s
        self._recorded: RecordedRun | None = None
        self._step = 0

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "provider": "replay",
            "model": "replay",
            "step_delay_s": self._delay,
        }
        if self._recorded is not None:
            info.update(
                replayed_run_id=self._recorded.source_run_id,
                original_model=self._recorded.model,
                original_prompt_version=self._recorded.prompt_version,
            )
        return info

    def bind_question(self, question: str) -> None:
        """Select the recorded run before the run starts, so that the run's config
        snapshot (`describe()`) names the replayed run. Raises UnknownDemoQuestionError."""
        recorded = self._catalog.get(question.strip())
        if recorded is None:
            raise UnknownDemoQuestionError(
                "demo mode replays recorded questions only; "
                f"{len(self._catalog)} recorded questions are available"
            )
        self._recorded = recorded

    def _id_map(self, messages: Sequence[Message], recorded: RecordedRun) -> dict[str, str]:
        """Recorded ID -> live ID, aligned by tool-result order."""
        mapping: dict[str, str] = {}
        live_datasets = {
            name: (source_id, view_id)
            for m in messages
            if m["role"] == "system"
            for name, source_id, view_id in _DATASET_LINE.findall(str(m["content"]))
        }
        for original in recorded.datasets:
            live = live_datasets.get(original["name"])
            if live:
                mapping[original["source_id"]] = live[0]
                mapping[original["root_view_id"]] = live[1]
        # A run's messages contain only its own tool results (earlier turns are text).
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        for live, original in zip(results, recorded.op_ids, strict=False):
            if "evidence_id" in live and original.get("evidence_id"):
                mapping[original["evidence_id"]] = live["evidence_id"]
            live_view = (live.get("result") or {}).get("view_id")
            for out in original.get("output_ids", []):
                if live_view:
                    mapping[out] = live_view
        return mapping

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]],
        timeout_s: float | None = None,
    ) -> ModelResponse:
        if self._recorded is None:  # not bound up front (direct use): bind from messages
            question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            try:
                self.bind_question(question)
            except UnknownDemoQuestionError as exc:
                raise LLMUnavailableError(str(exc)) from exc
        recorded = self._recorded
        assert recorded is not None
        if self._step >= len(recorded.steps):
            raise LLMUnavailableError("demo replay: recorded run has no further responses")
        step = recorded.steps[self._step]
        self._step += 1
        if self._delay:
            time.sleep(self._delay)  # makes progress visible in the UI during demos

        mapping = self._id_map(messages, recorded)

        def remap(arguments: str) -> str:
            for old, new in mapping.items():
                arguments = arguments.replace(old, new)
            return arguments

        return ModelResponse(
            content=step.get("content"),
            tool_calls=[
                ToolCallRequest(id=tc["id"], name=tc["name"], arguments=remap(tc["arguments"]))
                for tc in step.get("tool_calls", [])
            ],
            finish_reason=step.get("finish_reason"),
            latency_ms=int(self._delay * 1000),
        )


def record_run(session: Session, run_id: str) -> dict[str, Any]:
    """Catalog entry for a completed run: its model responses and produced IDs."""
    run = session.get(AnalysisRun, run_id)
    if run is None or run.status != "completed":
        raise ValueError(f"{run_id}: not found or not completed")
    question = session.scalar(
        select(ChatMessage.content).where(ChatMessage.run_id == run_id, ChatMessage.role == "user")
    )
    calls = session.scalars(
        select(ModelCall).where(ModelCall.run_id == run_id).order_by(ModelCall.step_no)
    ).all()
    ops = session.scalars(
        select(Operation).where(Operation.run_id == run_id).order_by(Operation.sequence_no)
    ).all()
    evidence = dict(
        session.execute(
            select(Evidence.operation_id, Evidence.id).where(
                Evidence.operation_id.in_([o.id for o in ops])
            )
        ).all()
    )
    datasets = []
    for entry in run.config_json.get("datasets", []):
        source = session.get(DatasetSource, entry["source_id"])
        if source is not None:
            datasets.append(
                {
                    "name": source.name,
                    "source_id": source.id,
                    "root_view_id": root_view_id(session, source.id),
                }
            )
    return {
        "source_run_id": run.id,
        "question": (question or "").strip(),
        "model": run.config_json.get("llm", {}).get("model", "?"),
        "prompt_version": run.config_json.get("prompt_version", "?"),
        "steps": [
            {
                "content": c.content,
                "tool_calls": c.tool_calls_json,
                "finish_reason": c.finish_reason,
            }
            for c in calls
        ],
        "op_ids": [
            {"evidence_id": evidence.get(o.id), "output_ids": o.output_ids_json} for o in ops
        ],
        "datasets": datasets,
        "recorded": {
            "wall_s": round((run.usage_json or {}).get("wall_ms", 0) / 1000, 1),
            "model_calls": len(calls),
            "failed_operations": sum(o.status == "failed" for o in ops),
        },
    }
