"""Timeline events: an ordered, replayable record of what happened in a conversation.

`emit` must be called inside the same transaction that writes the entity, so an event
never exists without its entity and vice versa. `sequence_no` is allocated per
conversation; the unique constraint turns a concurrent-writer race into a loud error
(the API allows one active run per conversation).
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import TimelineEvent


class EventType(StrEnum):
    USER_MESSAGE = "USER_MESSAGE"
    RUN_STARTED = "RUN_STARTED"
    TOOL_SUCCEEDED = "TOOL_SUCCEEDED"
    TOOL_FAILED = "TOOL_FAILED"
    VIEW_CREATED = "VIEW_CREATED"
    EVIDENCE_CREATED = "EVIDENCE_CREATED"
    ASSUMPTION_RECORDED = "ASSUMPTION_RECORDED"
    CLAIM_CREATED = "CLAIM_CREATED"
    ASSISTANT_MESSAGE = "ASSISTANT_MESSAGE"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"


class EntityType(StrEnum):
    MESSAGE = "message"
    RUN = "run"
    OPERATION = "operation"
    VIEW = "view"
    EVIDENCE = "evidence"
    ASSUMPTION = "assumption"
    CLAIM = "claim"


def emit(
    session: Session,
    *,
    conversation_id: str,
    event_type: EventType,
    entity_type: EntityType,
    entity_id: str,
    run_id: str | None = None,
    message_id: str | None = None,
) -> TimelineEvent:
    """Append an event. `message_id` is the chat message that caused it (for run events,
    the run's question), which lets a chat selection resolve to graph nodes."""
    current = session.scalar(
        select(func.max(TimelineEvent.sequence_no)).where(
            TimelineEvent.conversation_id == conversation_id
        )
    )
    event = TimelineEvent(
        conversation_id=conversation_id,
        run_id=run_id,
        message_id=message_id,
        event_type=str(event_type),
        entity_type=str(entity_type),
        entity_id=entity_id,
        sequence_no=(current or 0) + 1,
    )
    session.add(event)
    session.flush()
    return event
