"""M3a: timeline events, assumptions, the computed graph, and conversation context."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.model_client import FakeLLMClient, ModelResponse, ScriptStep, tool_call
from app.agent.orchestrator import AgentOrchestrator, RunResult, RunStatus
from app.config import Settings
from app.db.models import (
    AnalysisRun,
    Assumption,
    AssumptionLink,
    ChatMessage,
    Claim,
    DatasetView,
    Evidence,
    Operation,
    TimelineEvent,
)
from app.provenance.graph import claim_assumptions, run_graph

Messages = Sequence[dict[str, Any]]
ENTITY_MODELS = {
    "message": ChatMessage,
    "run": AnalysisRun,
    "operation": Operation,
    "view": DatasetView,
    "evidence": Evidence,
    "assumption": Assumption,
    "claim": Claim,
}


def calls(*tc: Any) -> ModelResponse:
    return ModelResponse(tool_calls=list(tc))


def payloads(messages: Messages) -> list[dict[str, Any]]:
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def run_agent(
    sessions: sessionmaker[Session],
    settings: Settings,
    script: list[ScriptStep],
    question: str = "Q",
    conversation_id: str | None = None,
) -> tuple[RunResult, FakeLLMClient]:
    llm = FakeLLMClient(script)
    return AgentOrchestrator(sessions, llm, settings).ask(question, conversation_id), llm


def filtered_run_script(view_id: str) -> list[ScriptStep]:
    """create_view (workclass != Private, which excludes missing rows) -> summarize ->
    answer with a claim-level caveat, a shared caveat, and an answer-level caveat."""

    def summarize_view(messages: Messages) -> ModelResponse:
        new_view = payloads(messages)[-1]["result"]["view_id"]
        return calls(
            tool_call(
                "summarize",
                {"view_id": new_view, "columns": ["hours_per_week"], "metrics": ["mean"]},
            )
        )

    def answer(messages: Messages) -> ModelResponse:
        summary_ev = payloads(messages)[-1]["evidence_id"]
        return calls(
            tool_call(
                "submit_answer",
                {
                    "explanation": "Non-private workers average 40.5 hours; 12 rows matched.",
                    "claims": [
                        {
                            "text": "Non-private workers average N hours.",
                            "evidence_ids": [summary_ev],
                            "caveats": ["Small sample.", "Shared caveat."],
                        },
                        {
                            "text": "The view excludes private-sector rows.",
                            "evidence_ids": [summary_ev],
                            "caveats": ["Shared caveat."],
                        },
                    ],
                    "caveats": ["Association only."],
                },
            )
        )

    return [
        calls(
            tool_call(
                "create_view",
                {
                    "view_id": view_id,
                    "filters": [{"column": "workclass", "op": "ne", "value": "Private"}],
                },
            )
        ),
        summarize_view,
        answer,
    ]


@pytest.fixture
def completed(
    session_factory: sessionmaker[Session], settings: Settings, sample_ids: dict[str, str]
) -> RunResult:
    result, _ = run_agent(session_factory, settings, filtered_run_script(sample_ids["view_id"]))
    assert result.status == RunStatus.COMPLETED
    return result


# --- timeline -----------------------------------------------------------------------


def test_timeline_is_ordered_complete_and_resolvable(
    session_factory: sessionmaker[Session], completed: RunResult
) -> None:
    with session_factory() as db:
        events = db.scalars(
            select(TimelineEvent)
            .where(TimelineEvent.conversation_id == completed.conversation_id)
            .order_by(TimelineEvent.sequence_no)
        ).all()
        types = [e.event_type for e in events]
        assert [e.sequence_no for e in events] == list(range(1, len(events) + 1))
        assert types[:2] == ["USER_MESSAGE", "RUN_STARTED"] and types[-1] == "RUN_COMPLETED"
        # Causal order within the run is preserved.
        assert types.index("VIEW_CREATED") < types.index("CLAIM_CREATED")
        assert types.index("CLAIM_CREATED") < types.index("ASSISTANT_MESSAGE")
        assert types.count("CLAIM_CREATED") == 2

        question = db.scalar(
            select(ChatMessage).where(
                ChatMessage.run_id == completed.run_id, ChatMessage.role == "user"
            )
        )
        assert question is not None
        for event in events:
            # Every event resolves to a real entity of the declared type ...
            model = ENTITY_MODELS[event.entity_type]
            assert db.get(model, event.entity_id) is not None, event.event_type
            # ... and is traceable to the chat message that caused it.
            assert event.message_id == question.id and event.run_id == completed.run_id
            assert event.occurred_at.tzinfo is not None


def test_failed_run_ends_with_run_failed(
    session_factory: sessionmaker[Session], settings: Settings, sample_ids: dict[str, str]
) -> None:
    bad = tool_call("summarize", "{not json")
    result, _ = run_agent(
        session_factory, settings.model_copy(update={"max_agent_steps": 1}), [calls(bad)]
    )
    with session_factory() as db:
        types = db.scalars(
            select(TimelineEvent.event_type)
            .where(TimelineEvent.run_id == result.run_id)
            .order_by(TimelineEvent.sequence_no)
        ).all()
    assert types == ["USER_MESSAGE", "RUN_STARTED", "TOOL_FAILED", "RUN_FAILED"]


# --- assumptions ----------------------------------------------------------------------


def test_assumptions_are_linked_where_stated(
    session_factory: sessionmaker[Session], completed: RunResult
) -> None:
    with session_factory() as db:
        assumptions = {
            a.text: a
            for a in db.scalars(select(Assumption).where(Assumption.run_id == completed.run_id))
        }
        assert {a.status for a in assumptions.values()} == {"not_tested"}
        links = db.scalars(select(AssumptionLink)).all()

        def targets(text: str) -> list[tuple[str | None, str | None, str | None]]:
            aid = assumptions[text].id
            return [
                (lk.claim_id, lk.operation_id, lk.message_id)
                for lk in links
                if lk.assumption_id == aid
            ]

        claim1, claim2 = completed.claim_ids
        assert targets("Small sample.") == [(claim1, None, None)]
        # Same text on two claims: one assumption, two links.
        assert sorted(targets("Shared caveat.")) == sorted(
            [(claim1, None, None), (claim2, None, None)]
        )
        # Answer-level caveat qualifies the answer message only, never each claim.
        (answer_link,) = targets("Association only.")
        assert answer_link[0] is None and answer_link[2] is not None

        templates = {
            a.template_key: a for a in assumptions.values() if a.source == "method_template"
        }
        assert {"missing_excluded", "mean_sensitivity"} <= set(templates)
        assert "rows with missing 'workclass'" in templates["missing_excluded"].text


def test_assumption_link_needs_exactly_one_target(
    session_factory: sessionmaker[Session], completed: RunResult
) -> None:
    with session_factory() as db:
        assumption = db.scalars(select(Assumption)).first()
        claim = db.scalars(select(Claim)).first()
        message = db.scalars(select(ChatMessage)).first()
        assert assumption and claim and message
        for kwargs in ({}, {"claim_id": claim.id, "message_id": message.id}):
            db.add(AssumptionLink(assumption_id=assumption.id, **kwargs))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


def test_derived_assumptions_follow_lineage(
    session_factory: sessionmaker[Session], completed: RunResult
) -> None:
    with session_factory() as db:
        bearing = claim_assumptions(db, completed.claim_ids[0])
    stated = {a["text"]: a["scope"] for a in bearing["stated"]}
    derived = {a["template_key"] for a in bearing["derived"]}
    assert stated == {
        "Small sample.": "claim",
        "Shared caveat.": "claim",
        "Association only.": "answer",  # linked to the answer, reported as such
    }
    assert len({a["text"] for a in bearing["derived"]}) == len(bearing["derived"])  # merged
    # The claim cites only the summary; the missing-value caveat comes from the
    # create_view that produced the summarized view, found by traversal.
    assert {"mean_sensitivity", "missing_excluded"} <= derived


# --- graph ------------------------------------------------------------------------------


def test_graph_edges_and_origins(
    session_factory: sessionmaker[Session], completed: RunResult
) -> None:
    with session_factory() as db:
        graph = run_graph(db, completed.run_id)
    ids = {n.id for n in graph.nodes}
    assert all(e.source in ids and e.target in ids for e in graph.edges)
    origins = {(e.type, e.origin) for e in graph.edges}
    assert {
        ("TRIGGERED", "backend_verified"),
        ("ANSWERS", "backend_verified"),
        ("CONSUMES", "backend_verified"),
        ("PRODUCES", "backend_verified"),
        ("DERIVED_FROM", "backend_verified"),
        ("SUPPORTS", "agent_proposed"),
        ("QUALIFIES", "agent_stated"),
        ("QUALIFIES", "method_template"),
    } <= origins
    types = {n.type for n in graph.nodes}
    assert {
        "run",
        "user_message",
        "assistant_message",
        "operation",
        "evidence",
        "view",
        "source",
        "claim",
        "assumption",
    } <= types
    # Time order is never turned into an edge.
    assert not any(e.type in {"PRECEDES", "FOLLOWS", "NEXT"} for e in graph.edges)


def test_graph_snapshot_at_sequence(
    session_factory: sessionmaker[Session], completed: RunResult
) -> None:
    with session_factory() as db:
        events = db.scalars(
            select(TimelineEvent)
            .where(TimelineEvent.run_id == completed.run_id)
            .order_by(TimelineEvent.sequence_no)
        ).all()
        seq = {e.event_type: e.sequence_no for e in reversed(events)}  # first occurrence
        started = run_graph(db, completed.run_id, at_sequence=seq["RUN_STARTED"])
        after_evidence = run_graph(db, completed.run_id, at_sequence=seq["EVIDENCE_CREATED"])
        full = run_graph(db, completed.run_id)

    def kinds(g: Any) -> set[str]:
        return {n.type for n in g.nodes}

    assert "claim" not in kinds(started) and "operation" not in kinds(started)
    assert {"source", "view", "run", "user_message"} <= kinds(started)  # pre-existing + started
    assert "evidence" in kinds(after_evidence) and "claim" not in kinds(after_evidence)
    assert "claim" in kinds(full)
    assert len(started.edges) < len(after_evidence.edges) < len(full.edges)


# --- conversation context (ADR-005 option A) --------------------------------------------


def test_follow_up_sees_questions_and_claims_not_explanations(
    session_factory: sessionmaker[Session],
    settings: Settings,
    completed: RunResult,
    sample_ids: dict[str, str],
) -> None:
    failing, _ = run_agent(
        session_factory,
        settings.model_copy(update={"max_agent_steps": 1}),
        [calls(tool_call("summarize", "{bad"))],
        "Second question?",
        completed.conversation_id,
    )
    _, llm = run_agent(
        session_factory,
        settings,
        [calls(tool_call("submit_answer", {"explanation": "x"}))],
        "Third question?",
        completed.conversation_id,
    )
    sent = llm.requests[0]
    contents = [m["content"] for m in sent]
    assert sent[-1] == {"role": "user", "content": "Third question?"}
    assert "Q" in contents and "Second question?" in contents
    assert any("Non-private workers average N hours." in c for c in contents)
    assert not any("40.5" in c for c in contents)  # explanation text is never carried over
    assert any("did not finish" in c and "BUDGET_EXHAUSTED" in c for c in contents)
    # The stable prefix stays first (prompt cache).
    assert [m["role"] for m in sent[:2]] == ["system", "system"]
