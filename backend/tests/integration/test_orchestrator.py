"""Agent loop with a scripted fake model: provenance, repair, budgets, failure paths."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent import tool_registry
from app.agent.model_client import (
    FakeLLMClient,
    LLMUnavailableError,
    ModelResponse,
    ScriptStep,
    tool_call,
)
from app.agent.orchestrator import AgentOrchestrator, RunStatus
from app.agent.prompts import NUDGE_SUBMIT_ANSWER, PROMPT_VERSION
from app.analysis.tools import ToolContext, summarize
from app.config import Settings
from app.db.models import (
    AnalysisRun,
    ChatMessage,
    Claim,
    ClaimEvidence,
    Evidence,
    ModelCall,
    Operation,
)
from app.schemas.tools import SummarizeInput

Messages = Sequence[dict[str, Any]]


# --- helpers ------------------------------------------------------------------------


def calls(*tool_calls: Any) -> ModelResponse:
    return ModelResponse(tool_calls=list(tool_calls), finish_reason="tool_calls")


def tool_payloads(messages: Messages) -> list[dict[str, Any]]:
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def evidence_ids_seen(messages: Messages) -> list[str]:
    return [p["evidence_id"] for p in tool_payloads(messages) if "evidence_id" in p]


def submit_citing_all(text: str = "Men work more hours on average than women.") -> ScriptStep:
    def step(messages: Messages) -> ModelResponse:
        return calls(
            tool_call(
                "submit_answer",
                {
                    "explanation": "Summary of the comparison.",
                    "claims": [{"text": text, "evidence_ids": evidence_ids_seen(messages)}],
                    "caveats": ["Association only."],
                },
            )
        )

    return step


def summarize_call(view_id: str, **overrides: Any) -> Any:
    args = {
        "view_id": view_id,
        "columns": ["hours_per_week"],
        "group_by": ["sex"],
        "metrics": ["mean"],
    }
    return tool_call("summarize", {**args, **overrides})


def ops(db: Session, run_id: str) -> list[Operation]:
    return list(
        db.scalars(
            select(Operation).where(Operation.run_id == run_id).order_by(Operation.sequence_no)
        )
    )


@pytest.fixture
def agent_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"max_agent_steps": 6, "max_tool_calls_per_step": 4})


def make_agent(
    sessions: sessionmaker[Session], settings: Settings, script: list[ScriptStep], **kw: Any
) -> tuple[AgentOrchestrator, FakeLLMClient]:
    llm = FakeLLMClient(script)
    return AgentOrchestrator(sessions, llm, settings, **kw), llm


# --- happy path -----------------------------------------------------------------------


def test_question_to_grounded_claim(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    agent, llm = make_agent(
        session_factory,
        agent_settings,
        [
            calls(tool_call("inspect_dataset", {"source_id": sample_ids["source_id"]})),
            calls(summarize_call(sample_ids["view_id"])),
            submit_citing_all(),
        ],
    )
    result = agent.ask("Do men and women work different hours?")
    assert result.status == RunStatus.COMPLETED

    with session_factory() as db:
        run = db.get(AnalysisRun, result.run_id)
        assert run is not None and run.completed_at is not None
        assert run.config_json["prompt_version"] == PROMPT_VERSION
        assert run.config_json["tool_schema_hash"] == tool_registry.tool_schema_hash()
        assert run.config_json["llm"]["provider"] == "fake"
        assert run.usage_json is not None and run.usage_json["model_calls"] == 3

        operations = ops(db, run.id)
        assert [(o.tool_name, o.status) for o in operations] == [
            ("inspect_dataset", "succeeded"),
            ("summarize", "succeeded"),
        ]
        assert operations[1].input_ids_json == [sample_ids["view_id"]]

        (claim,) = db.scalars(select(Claim).where(Claim.run_id == run.id)).all()
        assert claim.id in result.claim_ids and claim.status == "proposed"
        links = db.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == claim.id)).all()
        assert {link.origin for link in links} == {"agent_proposed"}
        cited = {link.evidence_id for link in links}
        produced = set(
            db.scalars(select(Evidence.id).join(Operation).where(Operation.run_id == run.id))
        )
        assert cited == produced and len(cited) == 2

        # Replay: the stored evidence equals a direct, LLM-free tool call.
        summarize_evidence = db.scalar(
            select(Evidence).where(Evidence.operation_id == operations[1].id)
        )
        assert summarize_evidence is not None
        ctx = ToolContext(db, agent_settings.data_dir, agent_settings.max_tool_result_rows)
        replay = summarize(ctx, SummarizeInput.model_validate(operations[1].args_json))
        assert summarize_evidence.result_json == replay.model_dump(mode="json")

        messages = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == run.conversation_id)
            .order_by(ChatMessage.sequence_no)
        ).all()
        assert [(m.role, m.sequence_no, m.run_id) for m in messages] == [
            ("user", 1, run.id),
            ("assistant", 2, run.id),
        ]
        assert "Caveats:" in messages[1].content

    # The model received real evidence IDs in tool results.
    assert set(evidence_ids_seen(llm.requests[-1])) == produced


def test_follow_up_in_same_conversation(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    script: list[ScriptStep] = [calls(summarize_call(sample_ids["view_id"])), submit_citing_all()]
    first = make_agent(session_factory, agent_settings, list(script))[0].ask("Q1")
    second = make_agent(session_factory, agent_settings, list(script))[0].ask(
        "Q2", conversation_id=first.conversation_id
    )
    assert second.conversation_id == first.conversation_id
    with session_factory() as db:
        seqs = db.scalars(
            select(ChatMessage.sequence_no)
            .where(ChatMessage.conversation_id == first.conversation_id)
            .order_by(ChatMessage.sequence_no)
        ).all()
    assert seqs == [1, 2, 3, 4]
    with pytest.raises(ValueError, match="unknown conversation"):
        make_agent(session_factory, agent_settings, [])[0].ask("Q", conversation_id="conv_nope")


# --- failed calls and repair ------------------------------------------------------------


def test_invalid_arguments_recorded_without_evidence_then_repaired(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    bad = summarize_call(sample_ids["view_id"], columns=["hours_per_week", "sex"])
    agent, llm = make_agent(
        session_factory,
        agent_settings,
        [calls(bad), calls(summarize_call(sample_ids["view_id"])), submit_citing_all()],
    )
    result = agent.ask("Hours by sex?")
    assert result.status == RunStatus.COMPLETED

    error = tool_payloads(llm.requests[1])[0]["error"]
    assert error["code"] == "INVALID_ARGUMENTS" and "overlap" in error["message"]
    with session_factory() as db:
        failed, succeeded = ops(db, result.run_id)
        assert failed.status == "failed" and failed.args_json is None
        assert failed.raw_arguments == bad.arguments  # recorded exactly as sent
        assert db.scalar(select(Evidence).where(Evidence.operation_id == failed.id)) is None
        assert succeeded.status == "succeeded"


def test_unknown_tool_and_malformed_json(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    agent, llm = make_agent(
        session_factory,
        agent_settings,
        [
            calls(tool_call("run_sql", {"q": "DROP TABLE x"}), tool_call("summarize", "{not json")),
            calls(tool_call("submit_answer", {"explanation": "Cannot answer.", "claims": []})),
        ],
    )
    result = agent.ask("Anything")
    assert result.status == RunStatus.COMPLETED and result.claim_ids == []
    codes = [p["error"]["code"] for p in tool_payloads(llm.requests[1])]
    assert codes == ["UNKNOWN_TOOL", "INVALID_JSON"]
    with session_factory() as db:
        assert [o.status for o in ops(db, result.run_id)] == ["failed", "failed"]


def test_tool_crash_is_recorded_and_run_continues(
    session_factory: sessionmaker[Session],
    agent_settings: Settings,
    sample_ids: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(ctx: Any, args: Any) -> Any:
        raise RuntimeError("bug")

    spec = tool_registry.TOOLS["inspect_dataset"]
    monkeypatch.setitem(
        tool_registry.TOOLS,
        "inspect_dataset",
        tool_registry.ToolSpec(spec.name, spec.description, spec.input_model, boom, spec.input_ids),
    )
    agent, llm = make_agent(
        session_factory,
        agent_settings,
        [
            calls(tool_call("inspect_dataset", {"source_id": sample_ids["source_id"]})),
            calls(tool_call("submit_answer", {"explanation": "No data.", "claims": []})),
        ],
    )
    result = agent.ask("Profile it")
    assert result.status == RunStatus.COMPLETED
    assert tool_payloads(llm.requests[1])[0]["error"]["code"] == "INTERNAL_ERROR"


# --- citations ----------------------------------------------------------------------------


def test_fabricated_citation_rejected_then_repaired(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    fabricated = calls(
        tool_call(
            "submit_answer",
            {
                "explanation": "x",
                "claims": [{"text": "Men work 99 hours.", "evidence_ids": ["ev_invented"]}],
            },
        )
    )
    agent, llm = make_agent(
        session_factory,
        agent_settings,
        [calls(summarize_call(sample_ids["view_id"])), fabricated, submit_citing_all()],
    )
    result = agent.ask("Hours by sex?")
    assert result.status == RunStatus.COMPLETED

    error = tool_payloads(llm.requests[2])[-1]["error"]
    assert error["code"] == "INVALID_CITATIONS"
    assert error["details"]["unknown_evidence_ids"] == ["ev_invented"]
    assert len(error["details"]["valid_evidence_ids"]) == 1
    with session_factory() as db:
        texts = db.scalars(select(Claim.text).where(Claim.run_id == result.run_id)).all()
        # The rejected answer is recorded as a failed attempt (no evidence).
        attempts = [(o.tool_name, o.status) for o in ops(db, result.run_id)]
        run = db.get(AnalysisRun, result.run_id)
        assert run is not None and run.usage_json is not None
        assert run.usage_json["failed_operations"] == 1
    assert texts == ["Men work more hours on average than women."]  # rejected claim not stored
    assert attempts == [("summarize", "succeeded"), ("submit_answer", "failed")]


def test_cannot_cite_evidence_from_another_run(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    first = make_agent(
        session_factory,
        agent_settings,
        [calls(summarize_call(sample_ids["view_id"])), submit_citing_all()],
    )[0].ask("Q1")
    with session_factory() as db:
        foreign = db.scalar(
            select(Evidence.id).join(Operation).where(Operation.run_id == first.run_id)
        )
    steal = calls(
        tool_call(
            "submit_answer",
            {"explanation": "x", "claims": [{"text": "t", "evidence_ids": [foreign]}]},
        )
    )
    settings = agent_settings.model_copy(update={"max_agent_steps": 2})
    second = make_agent(session_factory, settings, [steal, steal])[0].ask("Q2")

    assert second.status == RunStatus.FAILED
    assert second.error is not None and second.error["code"] == "BUDGET_EXHAUSTED"
    assert second.error["details"]["last_error"]["code"] == "INVALID_CITATIONS"
    with session_factory() as db:
        assert db.scalars(select(Claim).where(Claim.run_id == second.run_id)).all() == []
        first_run = db.get(AnalysisRun, first.run_id)
        assert first_run is not None and first_run.status == RunStatus.COMPLETED


# --- budgets and failures -----------------------------------------------------------------


def test_step_budget_keeps_partial_provenance(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    inspect = calls(tool_call("inspect_dataset", {"source_id": sample_ids["source_id"]}))
    settings = agent_settings.model_copy(update={"max_agent_steps": 2})
    result = make_agent(session_factory, settings, [inspect, inspect])[0].ask("Loop forever")

    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["details"]["budget"] == "steps"
    with session_factory() as db:
        run = db.get(AnalysisRun, result.run_id)
        assert run is not None and run.completed_at is not None
        assert [o.status for o in ops(db, result.run_id)] == ["succeeded", "succeeded"]
        assert len(db.scalars(select(ModelCall).where(ModelCall.run_id == run.id)).all()) == 2


def test_time_budget(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    now = [0.0]

    def slow_step(messages: Messages) -> ModelResponse:
        now[0] += 100.0  # the model call "takes" 100 s
        return calls(tool_call("inspect_dataset", {"source_id": sample_ids["source_id"]}))

    settings = agent_settings.model_copy(update={"max_run_seconds": 50.0})
    agent, llm = make_agent(session_factory, settings, [slow_step], clock=lambda: now[0])
    result = agent.ask("Slow")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["details"]["budget"] == "time"
    assert llm.timeouts == [50.0]  # the call was capped at the remaining budget
    with session_factory() as db:  # the late response was recorded but not acted on
        assert ops(db, result.run_id) == []
        assert (
            len(db.scalars(select(ModelCall).where(ModelCall.run_id == result.run_id)).all()) == 1
        )


def test_model_sees_missing_key_groups_and_undefined_stats(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    call = tool_call(
        "summarize",
        {
            "view_id": sample_ids["view_id"],
            "columns": ["hours_per_week"],
            "group_by": ["native_country"],
            "metrics": ["mean", "std"],
        },
    )
    agent, llm = make_agent(session_factory, agent_settings, [calls(call), submit_citing_all()])
    agent.ask("Hours by country")
    groups = tool_payloads(llm.requests[1])[0]["result"]["groups"]

    # The missing-value group keeps its explicit null key (not {}).
    assert {"native_country": None} in [g["key"] for g in groups]
    assert all("native_country" in g["key"] for g in groups)
    summaries = [g["columns"]["hours_per_week"] for g in groups]
    # Unrequested metrics are omitted; requested-but-undefined ones stay as explicit null.
    assert all("median" not in s and "mean" in s for s in summaries)
    singletons = [s for s in summaries if s["n"] == 1]
    assert singletons and all("std" in s and s["std"] is None for s in singletons)


def test_model_unavailable(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    agent, _ = make_agent(
        session_factory, agent_settings, [LLMUnavailableError("connection refused")]
    )
    result = agent.ask("Anything")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["code"] == "MODEL_UNAVAILABLE"
    with session_factory() as db:
        (call,) = db.scalars(select(ModelCall).where(ModelCall.run_id == result.run_id)).all()
        assert call.error_json is not None and call.error_json["code"] == "MODEL_UNAVAILABLE"


def test_unexpected_error_marks_run_failed(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    agent, _ = make_agent(session_factory, agent_settings, [RuntimeError("adapter bug")])
    result = agent.ask("Anything")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["code"] == "INTERNAL_ERROR"


def test_plain_text_gets_one_nudge_then_fails(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    text = ModelResponse(content="Men work about 42 hours.", finish_reason="stop")
    agent, llm = make_agent(session_factory, agent_settings, [text, text])
    result = agent.ask("Hours?")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["code"] == "NO_STRUCTURED_ANSWER"
    assert llm.requests[1][-1] == {"role": "user", "content": NUDGE_SUBMIT_ANSWER}


def test_plain_text_then_structured_answer_completes(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    text = ModelResponse(content="Let me answer.", finish_reason="stop")
    answer = calls(tool_call("submit_answer", {"explanation": "No claims.", "claims": []}))
    result = make_agent(session_factory, agent_settings, [text, answer])[0].ask("Hours?")
    assert result.status == RunStatus.COMPLETED


def test_too_many_tool_calls_per_step(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    inspect = tool_call("inspect_dataset", {"source_id": sample_ids["source_id"]})
    settings = agent_settings.model_copy(update={"max_tool_calls_per_step": 1})
    agent, llm = make_agent(
        session_factory,
        settings,
        [calls(inspect, inspect), calls(tool_call("submit_answer", {"explanation": "x"}))],
    )
    result = agent.ask("Hours?")
    assert result.status == RunStatus.COMPLETED
    payloads = tool_payloads(llm.requests[1])
    assert len(payloads) == 2  # every tool call gets a reply (protocol requirement)
    assert payloads[1]["error"]["code"] == "TOO_MANY_TOOL_CALLS"
    with session_factory() as db:
        assert [o.status for o in ops(db, result.run_id)] == ["succeeded", "failed"]


def test_oversized_result_hidden_from_model_but_stored(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    big = tool_call(
        "summarize",
        {
            "view_id": sample_ids["view_id"],
            "columns": ["education", "occupation", "native_country"],
            "group_by": ["workclass"],
            "metrics": ["proportions"],
        },
    )
    settings = agent_settings.model_copy(update={"max_tool_result_chars": 500})
    agent, llm = make_agent(session_factory, settings, [calls(big), submit_citing_all()])
    result = agent.ask("Big")
    payload = tool_payloads(llm.requests[1])[0]
    assert "notice" in payload and "result" not in payload
    with session_factory() as db:
        evidence = db.get(Evidence, payload["evidence_id"])
        assert evidence is not None and len(json.dumps(evidence.result_json)) > 500
    assert result.status == RunStatus.COMPLETED


def test_no_datasets_registered(
    session_factory: sessionmaker[Session], agent_settings: Settings
) -> None:
    result = make_agent(session_factory, agent_settings, [])[0].ask("Anything")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["code"] == "NO_DATASETS"


def test_no_write_transaction_held_during_model_call(
    session_factory: sessionmaker[Session],
    agent_settings: Settings,
    sample_ids: dict[str, str],
    database_url: str,
) -> None:
    db_path = database_url.removeprefix("sqlite:///")

    def assert_db_writable(messages: Messages) -> ModelResponse:
        probe = sqlite3.connect(db_path, timeout=0, isolation_level=None)
        try:
            probe.execute("BEGIN IMMEDIATE")  # fails with 'database is locked' if a writer holds it
            probe.execute("ROLLBACK")
        finally:
            probe.close()
        return calls(tool_call("submit_answer", {"explanation": "ok"}))

    agent, _ = make_agent(
        session_factory,
        agent_settings,
        [lambda m: calls(summarize_call(sample_ids["view_id"])), assert_db_writable],
    )
    assert agent.ask("Hours?").status == RunStatus.COMPLETED


# --- context window guard ------------------------------------------------------------------


def test_context_guard_blocks_oversized_initial_prompt(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    settings = agent_settings.model_copy(
        update={"llm_context_tokens": 1024, "llm_output_reserve_tokens": 512}
    )
    agent, llm = make_agent(session_factory, settings, [])
    result = agent.ask("Anything")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["code"] == "CONTEXT_EXHAUSTED"
    assert llm.requests == []  # the model was never called with a prompt it can't hold


def test_context_guard_rejects_response_at_limit(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    near_limit = ModelResponse(
        tool_calls=[summarize_call(sample_ids["view_id"])], prompt_tokens=7900, completion_tokens=50
    )
    result = make_agent(session_factory, agent_settings, [near_limit])[0].ask("Hours?")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["code"] == "CONTEXT_EXHAUSTED"
    assert "truncated" in result.error["message"]
    with session_factory() as db:  # the untrusted response was not acted on
        assert ops(db, result.run_id) == []


def test_context_guard_catches_growth_between_steps(
    session_factory: sessionmaker[Session], agent_settings: Settings, sample_ids: dict[str, str]
) -> None:
    big = tool_call(
        "summarize",
        {
            "view_id": sample_ids["view_id"],
            "columns": ["education", "occupation", "native_country"],
            "group_by": ["workclass"],
            "metrics": ["proportions"],
        },
    )
    first = ModelResponse(tool_calls=[big], prompt_tokens=5000, completion_tokens=100)
    settings = agent_settings.model_copy(update={"max_tool_result_chars": 50_000})
    agent, llm = make_agent(session_factory, settings, [first])
    result = agent.ask("Everything by workclass")
    assert result.status == RunStatus.FAILED
    assert result.error is not None and result.error["code"] == "CONTEXT_EXHAUSTED"
    assert result.error["details"]["estimated_prompt_tokens"] > 7168
    assert len(llm.requests) == 1  # second call was not sent
    with session_factory() as db:  # the evidence computed before the limit is kept
        assert [o.status for o in ops(db, result.run_id)] == ["succeeded"]
