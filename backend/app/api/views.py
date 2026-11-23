"""Read models: database records -> API schemas. Shared by the route modules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    Assumption,
    ChatMessage,
    Claim,
    ClaimEvidence,
    Evidence,
    Operation,
    TimelineEvent,
)
from app.provenance.graph import claim_assumptions
from app.provenance.text_checks import explanation_part, unverified_numbers
from app.schemas.api import (
    AssumptionOut,
    ClaimAssumptions,
    ClaimOut,
    MessageOut,
    OperationOut,
    RunDetail,
)


def message_out(session: Session, message: ChatMessage) -> MessageOut:
    out = MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        run_id=message.run_id,
        role=message.role,  # type: ignore[arg-type]
        content=message.content,
        sequence_no=message.sequence_no,
        created_at=message.created_at,
    )
    if message.role == "assistant" and message.run_id is not None:
        claim_texts = session.scalars(select(Claim.text).where(Claim.run_id == message.run_id))
        out.verified = False
        out.unverified_numbers = unverified_numbers(explanation_part(message.content), claim_texts)
    return out


def _assumption_out(entry: dict[str, Any]) -> AssumptionOut:
    return AssumptionOut.model_validate(entry)


def run_detail(session: Session, run: AnalysisRun) -> RunDetail:
    messages = session.scalars(select(ChatMessage).where(ChatMessage.run_id == run.id)).all()
    question = next((m for m in messages if m.role == "user"), None)
    answer = next((m for m in messages if m.role == "assistant"), None)

    evidence_by_op = dict(
        session.execute(
            select(Evidence.operation_id, Evidence.id)
            .join(Operation, Evidence.operation_id == Operation.id)
            .where(Operation.run_id == run.id)
        ).all()
    )
    operations = [
        OperationOut(
            id=op.id,
            sequence_no=op.sequence_no,
            tool_name=op.tool_name,
            status=op.status,  # type: ignore[arg-type]
            raw_arguments=op.raw_arguments,
            args=op.args_json,
            error=op.error_json,
            input_ids=op.input_ids_json,
            output_ids=op.output_ids_json,
            evidence_id=evidence_by_op.get(op.id),
            started_at=op.started_at,
            completed_at=op.completed_at,
        )
        for op in session.scalars(
            select(Operation).where(Operation.run_id == run.id).order_by(Operation.sequence_no)
        )
    ]

    claims = []
    for claim in session.scalars(
        select(Claim).where(Claim.run_id == run.id).order_by(Claim.sequence_no)
    ):
        cited = session.scalars(
            select(ClaimEvidence.evidence_id).where(ClaimEvidence.claim_id == claim.id)
        ).all()
        bearing = claim_assumptions(session, claim.id)
        claims.append(
            ClaimOut(
                id=claim.id,
                sequence_no=claim.sequence_no,
                text=claim.text,
                status=claim.status,
                evidence_ids=list(cited),
                assumptions=ClaimAssumptions(
                    stated=[_assumption_out(a) for a in bearing["stated"]],
                    derived=[_assumption_out(a) for a in bearing["derived"]],
                ),
            )
        )

    assumptions = [
        AssumptionOut(
            id=a.id,
            text=a.text,
            source=a.source,  # type: ignore[arg-type]
            status=a.status,
            template_key=a.template_key,
        )
        for a in session.scalars(
            select(Assumption).where(Assumption.run_id == run.id).order_by(Assumption.created_at)
        )
    ]
    last_seq = session.scalar(
        select(func.max(TimelineEvent.sequence_no)).where(TimelineEvent.run_id == run.id)
    )
    return RunDetail(
        id=run.id,
        conversation_id=run.conversation_id,
        parent_run_id=run.parent_run_id,
        status=run.status,  # type: ignore[arg-type]
        created_at=run.created_at,
        completed_at=run.completed_at,
        error=run.error_json,
        usage=run.usage_json,
        config=run.config_json,
        question=message_out(session, question) if question else None,
        answer=message_out(session, answer) if answer else None,
        operations=operations,
        claims=claims,
        assumptions=assumptions,
        last_event_sequence=last_seq,
    )
