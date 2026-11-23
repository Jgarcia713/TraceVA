"""Agent loop: question -> model -> validated tool execution -> evidence -> grounded claims.

Persistence rules (brief §6, §8, §11):
- Every tool call the model makes is recorded as an Operation, written once, with
  Evidence only on success. Failed calls produce no evidence.
- Claims may cite only evidence produced by successful operations of the same run.
- Database transactions are short and never span a model call or a tool computation:
  tools run in a read session that is closed before the write transaction opens.
- Conversations group runs, but each run's model context contains only its own
  question. Carrying earlier turns into the context is an M3 design decision.
- Every exit path leaves the run `completed` or `failed` with a typed error, and the
  partial record is kept.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model_client import LLMClient, LLMUnavailableError, ModelResponse, ToolCallRequest
from app.agent.prompts import NUDGE_SUBMIT_ANSWER, PROMPT_VERSION, initial_messages
from app.agent.tool_registry import (
    SUBMIT_ANSWER,
    ToolOutcome,
    execute,
    model_tool_schemas,
    parse_arguments,
    summarize_validation_error,
    tool_schema_hash,
)
from app.analysis.registry import root_view_id
from app.analysis.tools import ToolContext
from app.config import Settings
from app.db.base import utcnow
from app.db.models import (
    AnalysisRun,
    ChatMessage,
    Claim,
    ClaimEvidence,
    Conversation,
    DatasetSource,
    Evidence,
    ModelCall,
    Operation,
)
from app.schemas.agent import SubmitAnswerInput
from app.schemas.tools import ToolErrorCode

logger = logging.getLogger(__name__)


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunFailure(StrEnum):
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    NO_STRUCTURED_ANSWER = "NO_STRUCTURED_ANSWER"
    NO_DATASETS = "NO_DATASETS"
    CONTEXT_EXHAUSTED = "CONTEXT_EXHAUSTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class RunResult:
    run_id: str
    conversation_id: str
    status: RunStatus
    claim_ids: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None


@dataclass
class _Usage:
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_latency_ms: int = 0
    reasoning_chars_discarded: int = 0
    operations: int = 0
    failed_operations: int = 0

    def add(self, response: ModelResponse) -> None:
        self.model_calls += 1
        self.prompt_tokens += response.prompt_tokens or 0
        self.completion_tokens += response.completion_tokens or 0
        self.model_latency_ms += response.latency_ms
        self.reasoning_chars_discarded += response.reasoning_chars_discarded


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


def _assistant_message(response: ModelResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in response.tool_calls
        ],
    }


CHARS_PER_TOKEN_ESTIMATE = 3.0  # conservative (overestimates tokens) for JSON-heavy text


def _estimate_tokens(payload: Any) -> int:
    return int(len(json.dumps(payload)) / CHARS_PER_TOKEN_ESTIMATE) + 1


def _tool_message(call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}


class AgentOrchestrator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        llm: LLMClient,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = session_factory
        self._llm = llm
        self._settings = settings
        self._clock = clock

    # ------------------------------------------------------------------ public

    def ask(self, question: str, conversation_id: str | None = None) -> RunResult:
        started = self._clock()
        usage = _Usage()
        datasets = self._dataset_context()
        run_id, conversation_id = self._start_run(question, conversation_id, datasets)
        try:
            return self._loop(run_id, conversation_id, question, datasets, usage, started)
        except Exception as exc:  # never leave a run silently 'running'
            logger.exception("run %s failed unexpectedly", run_id)
            return self._fail(
                run_id,
                conversation_id,
                usage,
                started,
                _error(RunFailure.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"),
            )

    # ------------------------------------------------------------------ loop

    def _loop(
        self,
        run_id: str,
        conversation_id: str,
        question: str,
        datasets: list[dict[str, Any]],
        usage: _Usage,
        started: float,
    ) -> RunResult:
        s = self._settings
        if not datasets:
            return self._fail(
                run_id,
                conversation_id,
                usage,
                started,
                _error(RunFailure.NO_DATASETS, "no datasets are registered"),
            )
        messages = initial_messages(datasets, question)
        tools = model_tool_schemas()
        deadline = started + s.max_run_seconds
        nudged = False
        last_error: dict[str, Any] | None = None
        # (tokens already counted by the server, number of messages they covered)
        known: tuple[int, int] | None = None

        for step in range(1, s.max_agent_steps + 1):
            if self._clock() > deadline:
                return self._fail(
                    run_id, conversation_id, usage, started, self._time_budget_error(last_error)
                )
            estimate = self._estimate_prompt(messages, tools, known)
            if self._over_context(estimate):
                return self._fail(
                    run_id,
                    conversation_id,
                    usage,
                    started,
                    _error(
                        RunFailure.CONTEXT_EXHAUSTED,
                        "the next request would not fit the model's context window",
                        {
                            "estimated_prompt_tokens": estimate,
                            "step": step,
                            **self._context_budget(),
                        },
                    ),
                )
            remaining = max(deadline - self._clock(), 1.0)
            try:
                response = self._llm.complete(messages, tools, timeout_s=remaining)
            except LLMUnavailableError as exc:
                self._record_model_call(run_id, step, None, _error("MODEL_UNAVAILABLE", str(exc)))
                if self._clock() >= deadline:  # our shortened timeout expired
                    return self._fail(
                        run_id, conversation_id, usage, started, self._time_budget_error(last_error)
                    )
                return self._fail(
                    run_id,
                    conversation_id,
                    usage,
                    started,
                    _error(RunFailure.MODEL_UNAVAILABLE, str(exc), {"step": step}),
                )
            usage.add(response)
            model_call_id = self._record_model_call(run_id, step, response, None)
            if self._clock() > deadline:  # recorded, but not acted on
                return self._fail(
                    run_id, conversation_id, usage, started, self._time_budget_error(last_error)
                )
            if response.prompt_tokens is not None and self._over_context(response.prompt_tokens):
                # The server may already have dropped context; don't trust this response.
                return self._fail(
                    run_id,
                    conversation_id,
                    usage,
                    started,
                    _error(
                        RunFailure.CONTEXT_EXHAUSTED,
                        "prompt reached the context limit; the server may have truncated it",
                        {
                            "prompt_tokens": response.prompt_tokens,
                            "step": step,
                            **self._context_budget(),
                        },
                    ),
                )
            known = (
                (
                    (response.prompt_tokens or 0) + (response.completion_tokens or 0),
                    len(messages),
                )
                if response.prompt_tokens is not None
                else None
            )

            if not response.tool_calls:
                if nudged:
                    return self._fail(
                        run_id,
                        conversation_id,
                        usage,
                        started,
                        _error(
                            RunFailure.NO_STRUCTURED_ANSWER,
                            "model replied without calling submit_answer",
                            {"step": step},
                        ),
                    )
                nudged = True
                messages.append({"role": "assistant", "content": response.content or ""})
                messages.append({"role": "user", "content": NUDGE_SUBMIT_ANSWER})
                continue

            messages.append(_assistant_message(response))
            for index, call in enumerate(response.tool_calls):
                if index >= s.max_tool_calls_per_step:
                    payload = {
                        "error": self._record_rejected_call(
                            run_id,
                            model_call_id,
                            call,
                            usage,
                            _error(
                                ToolErrorCode.TOO_MANY_TOOL_CALLS,
                                f"at most {s.max_tool_calls_per_step} tool calls per step; "
                                "this call was not executed",
                            ),
                        )
                    }
                elif call.name == SUBMIT_ANSWER:
                    result = self._submit_answer(run_id, conversation_id, call, usage, started)
                    if isinstance(result, RunResult):
                        return result
                    # A rejected answer is a failed attempt and is recorded like one.
                    payload = {
                        "error": self._record_rejected_call(
                            run_id, model_call_id, call, usage, result
                        )
                    }
                else:
                    payload = self._execute_tool(run_id, model_call_id, call, usage)
                last_error = payload.get("error", last_error)
                messages.append(_tool_message(call.id, payload))

        return self._fail(
            run_id,
            conversation_id,
            usage,
            started,
            _error(
                RunFailure.BUDGET_EXHAUSTED,
                "step budget exhausted without an answer",
                {"budget": "steps", "max_agent_steps": s.max_agent_steps, "last_error": last_error},
            ),
        )

    def _time_budget_error(self, last_error: dict[str, Any] | None) -> dict[str, Any]:
        return _error(
            RunFailure.BUDGET_EXHAUSTED,
            "run time budget exhausted",
            {
                "budget": "time",
                "max_run_seconds": self._settings.max_run_seconds,
                "last_error": last_error,
            },
        )

    # ------------------------------------------------------------------ context

    def _context_budget(self) -> dict[str, Any]:
        s = self._settings
        return {
            "context_tokens": s.llm_context_tokens,
            "output_reserve_tokens": s.llm_output_reserve_tokens,
        }

    def _over_context(self, prompt_tokens: int) -> bool:
        s = self._settings
        if s.llm_context_tokens is None:
            return False
        return prompt_tokens > s.llm_context_tokens - s.llm_output_reserve_tokens

    @staticmethod
    def _estimate_prompt(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        known: tuple[int, int] | None,
    ) -> int:
        """Server-reported count for what it has seen + a conservative estimate for the rest.

        `known` counts prompt+completion tokens of the previous call; its completion is the
        assistant message appended right after, so only later messages are estimated.
        """
        if known is None:
            return _estimate_tokens(messages) + _estimate_tokens(tools)
        counted, covered = known
        return counted + _estimate_tokens(messages[covered + 1 :])

    # ------------------------------------------------------------------ tools

    def _next_op_seq(self, session: Session, run_id: str) -> int:
        current = session.scalar(
            select(func.max(Operation.sequence_no)).where(Operation.run_id == run_id)
        )
        return (current or 0) + 1

    def _execute_tool(
        self, run_id: str, model_call_id: str, call: ToolCallRequest, usage: _Usage
    ) -> dict[str, Any]:
        s = self._settings
        # Compute in a read-only session (closed afterwards), then write in a short
        # transaction, so a slow first dataset load never holds the database.
        with self._sessions() as read_session:
            ctx = ToolContext(read_session, s.data_dir, s.max_tool_result_rows)
            started_at = utcnow()
            outcome = execute(ctx, call.name, call.arguments)
            completed_at = utcnow()
        with self._sessions.begin() as session:
            operation = self._operation(
                session, run_id, model_call_id, call, outcome, started_at, completed_at
            )
            usage.operations += 1
            if not outcome.ok:
                usage.failed_operations += 1
                return {"error": outcome.error}
            assert outcome.result is not None and outcome.model_view is not None
            evidence = Evidence(operation_id=operation.id, result_json=outcome.result)
            session.add(evidence)
            session.flush()
            return self._model_view(evidence.id, outcome.model_view)

    def _record_rejected_call(
        self,
        run_id: str,
        model_call_id: str,
        call: ToolCallRequest,
        usage: _Usage,
        error: dict[str, Any],
    ) -> dict[str, Any]:
        outcome = ToolOutcome(args=None, input_ids=[], result=None, error=error)
        with self._sessions.begin() as session:
            now = utcnow()
            self._operation(session, run_id, model_call_id, call, outcome, now, now)
        usage.operations += 1
        usage.failed_operations += 1
        return error

    def _operation(
        self,
        session: Session,
        run_id: str,
        model_call_id: str,
        call: ToolCallRequest,
        outcome: ToolOutcome,
        started_at: Any,
        completed_at: Any,
    ) -> Operation:
        operation = Operation(
            run_id=run_id,
            model_call_id=model_call_id,
            sequence_no=self._next_op_seq(session, run_id),
            tool_call_id=call.id[:100],
            tool_name=call.name[:100],
            raw_arguments=call.arguments,
            args_json=outcome.args,
            input_ids_json=outcome.input_ids,
            status="succeeded" if outcome.ok else "failed",
            error_json=outcome.error,
            started_at=started_at,
            completed_at=completed_at,
        )
        session.add(operation)
        session.flush()
        return operation

    def _model_view(self, evidence_id: str, compact: dict[str, Any]) -> dict[str, Any]:
        """What the model sees: compact result + evidence ID. Evidence keeps the full result."""
        if len(json.dumps(compact)) > self._settings.max_tool_result_chars:
            return {
                "evidence_id": evidence_id,
                "notice": (
                    "The result was computed and stored, but it is too large to show. "
                    "Request fewer columns, fewer groups, or fewer metrics."
                ),
            }
        return {"evidence_id": evidence_id, "result": compact}

    # ------------------------------------------------------------------ answer

    def _submit_answer(
        self,
        run_id: str,
        conversation_id: str,
        call: ToolCallRequest,
        usage: _Usage,
        started: float,
    ) -> RunResult | dict[str, Any]:
        parsed = parse_arguments(call.arguments)
        if isinstance(parsed, ToolOutcome):
            assert parsed.error is not None
            return parsed.error
        try:
            answer = SubmitAnswerInput.model_validate(parsed)
        except ValidationError as exc:
            return _error(ToolErrorCode.INVALID_ARGUMENTS, summarize_validation_error(exc))

        cited = {eid for claim in answer.claims for eid in claim.evidence_ids}
        with self._sessions.begin() as session:
            run_evidence = set(
                session.scalars(
                    select(Evidence.id)
                    .join(Operation, Evidence.operation_id == Operation.id)
                    .where(Operation.run_id == run_id)
                )
            )
            unknown = sorted(cited - run_evidence)
            if unknown:
                return _error(
                    ToolErrorCode.INVALID_CITATIONS,
                    "claims cite evidence_ids that were not produced in this run",
                    {"unknown_evidence_ids": unknown, "valid_evidence_ids": sorted(run_evidence)},
                )

            claim_ids: list[str] = []
            for seq, draft in enumerate(answer.claims, start=1):
                claim = Claim(run_id=run_id, sequence_no=seq, text=draft.text, status="proposed")
                session.add(claim)
                session.flush()
                claim_ids.append(claim.id)
                for evidence_id in dict.fromkeys(draft.evidence_ids):  # dedupe, keep order
                    session.add(
                        ClaimEvidence(
                            claim_id=claim.id, evidence_id=evidence_id, origin="agent_proposed"
                        )
                    )

            content = answer.explanation
            if answer.caveats:
                content += "\n\nCaveats:\n" + "\n".join(f"- {c}" for c in answer.caveats)
            session.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role="assistant",
                    content=content,
                    sequence_no=self._next_message_seq(session, conversation_id),
                )
            )
            run = session.get(AnalysisRun, run_id)
            assert run is not None
            run.status = RunStatus.COMPLETED
            run.completed_at = utcnow()
            run.usage_json = self._usage_json(usage, started)
        return RunResult(run_id, conversation_id, RunStatus.COMPLETED, claim_ids)

    # ------------------------------------------------------------------ records

    def _dataset_context(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            sources = session.scalars(select(DatasetSource).order_by(DatasetSource.created_at))
            return [
                {
                    "source_id": src.id,
                    "name": src.name,
                    "sha256": src.sha256,
                    "root_view_id": root_view_id(session, src.id),
                    "row_count": src.row_count,
                    "columns": src.schema_json,
                }
                for src in sources
            ]

    def _next_message_seq(self, session: Session, conversation_id: str) -> int:
        current = session.scalar(
            select(func.max(ChatMessage.sequence_no)).where(
                ChatMessage.conversation_id == conversation_id
            )
        )
        return (current or 0) + 1

    def _start_run(
        self, question: str, conversation_id: str | None, datasets: list[dict[str, Any]]
    ) -> tuple[str, str]:
        s = self._settings
        config = {
            "llm": self._llm.describe(),
            "prompt_version": PROMPT_VERSION,
            "tool_schema_hash": tool_schema_hash(),
            "budgets": {
                "max_agent_steps": s.max_agent_steps,
                "max_run_seconds": s.max_run_seconds,
                "max_tool_calls_per_step": s.max_tool_calls_per_step,
                "max_tool_result_rows": s.max_tool_result_rows,
                "max_tool_result_chars": s.max_tool_result_chars,
            },
            "datasets": [{"source_id": d["source_id"], "sha256": d["sha256"]} for d in datasets],
        }
        with self._sessions.begin() as session:
            if conversation_id is None:
                conversation = Conversation()
                session.add(conversation)
                session.flush()
                conversation_id = conversation.id
            elif session.get(Conversation, conversation_id) is None:
                raise ValueError(f"unknown conversation: {conversation_id}")
            run = AnalysisRun(
                conversation_id=conversation_id, status=RunStatus.RUNNING, config_json=config
            )
            session.add(run)
            session.flush()
            session.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    run_id=run.id,
                    role="user",
                    content=question,
                    sequence_no=self._next_message_seq(session, conversation_id),
                )
            )
            return run.id, conversation_id

    def _record_model_call(
        self,
        run_id: str,
        step: int,
        response: ModelResponse | None,
        error: dict[str, Any] | None,
    ) -> str:
        with self._sessions.begin() as session:
            call = ModelCall(
                run_id=run_id,
                step_no=step,
                content=response.content if response else None,
                tool_calls_json=[tc.model_dump() for tc in response.tool_calls] if response else [],
                finish_reason=response.finish_reason if response else None,
                usage_json=(
                    {
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                    }
                    if response
                    else None
                ),
                latency_ms=response.latency_ms if response else 0,
                reasoning_chars_discarded=response.reasoning_chars_discarded if response else 0,
                error_json=error,
            )
            session.add(call)
            session.flush()
            return call.id

    def _usage_json(self, usage: _Usage, started: float) -> dict[str, Any]:
        return {**usage.__dict__, "wall_ms": int((self._clock() - started) * 1000)}

    def _fail(
        self,
        run_id: str,
        conversation_id: str,
        usage: _Usage,
        started: float,
        error: dict[str, Any],
    ) -> RunResult:
        with self._sessions.begin() as session:
            run = session.get(AnalysisRun, run_id)
            assert run is not None
            run.status = RunStatus.FAILED
            run.error_json = error
            run.completed_at = utcnow()
            run.usage_json = self._usage_json(usage, started)
        return RunResult(run_id, conversation_id, RunStatus.FAILED, error=error)
