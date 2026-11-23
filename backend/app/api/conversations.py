"""Conversations, messages (asking questions), timeline, and chat-to-graph resolution."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select

from app.agent.orchestrator import ConversationBusyError, RunStatus, UnknownConversationError
from app.api.deps import RunManagerDep, SessionDep
from app.api.errors import ApiError
from app.api.views import message_out
from app.db.models import AnalysisRun, ChatMessage, Conversation, TimelineEvent
from app.schemas.api import (
    ConversationOut,
    EventOut,
    EventPage,
    MessagePage,
    PostMessageIn,
    RelatedOut,
    RunAccepted,
    RunLinks,
)

router = APIRouter(tags=["conversations"])
MAX_PAGE = 500


def _conversation(session: SessionDep, conversation_id: str) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise ApiError(404, "UNKNOWN_CONVERSATION", f"unknown conversation: {conversation_id}")
    return conversation


def _active_run(session: SessionDep, conversation_id: str) -> str | None:
    return session.scalar(
        select(AnalysisRun.id).where(
            AnalysisRun.conversation_id == conversation_id,
            AnalysisRun.status == RunStatus.RUNNING,
        )
    )


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(session: SessionDep) -> ConversationOut:
    conversation = Conversation()
    session.add(conversation)
    session.commit()
    return ConversationOut(
        id=conversation.id, created_at=conversation.created_at, active_run_id=None
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: str, session: SessionDep) -> ConversationOut:
    conversation = _conversation(session, conversation_id)
    return ConversationOut(
        id=conversation.id,
        created_at=conversation.created_at,
        active_run_id=_active_run(session, conversation_id),
    )


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
def list_messages(
    conversation_id: str,
    session: SessionDep,
    after: int = Query(0, ge=0, description="Return messages with sequence_no > after."),
    limit: int = Query(100, ge=1, le=MAX_PAGE),
) -> MessagePage:
    _conversation(session, conversation_id)
    rows = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.sequence_no > after)
        .order_by(ChatMessage.sequence_no)
        .limit(limit + 1)
    ).all()
    items = [message_out(session, m) for m in rows[:limit]]
    next_after = items[-1].sequence_no if len(rows) > limit else None
    return MessagePage(items=items, next_after=next_after)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_message(
    conversation_id: str, body: PostMessageIn, manager: RunManagerDep, request: Request
) -> RunAccepted:
    """Ask a question. The run executes in the background; poll `links.run`."""
    try:
        pending = manager.submit(body.content, conversation_id)
    except UnknownConversationError as exc:
        raise ApiError(404, "UNKNOWN_CONVERSATION", str(exc)) from exc
    except ConversationBusyError as exc:
        raise ApiError(
            409,
            "CONVERSATION_BUSY",
            "this conversation already has a running run; wait for it to finish",
            {"active_run_id": exc.active_run_id},
        ) from exc
    prefix = request.app.state.api_prefix
    return RunAccepted(
        run_id=pending.run_id,
        conversation_id=pending.conversation_id,
        message_id=pending.question_message_id,
        status="running",
        links=RunLinks(
            run=f"{prefix}/runs/{pending.run_id}",
            graph=f"{prefix}/runs/{pending.run_id}/graph",
            timeline=f"{prefix}/conversations/{pending.conversation_id}/timeline",
        ),
    )


@router.get("/conversations/{conversation_id}/timeline", response_model=EventPage)
def timeline(
    conversation_id: str,
    session: SessionDep,
    after: int = Query(0, ge=0, description="Return events with sequence_no > after."),
    limit: int = Query(200, ge=1, le=MAX_PAGE),
) -> EventPage:
    """Chronological events. Order is temporal only; dependencies live in the graph."""
    _conversation(session, conversation_id)
    rows = session.scalars(
        select(TimelineEvent)
        .where(TimelineEvent.conversation_id == conversation_id, TimelineEvent.sequence_no > after)
        .order_by(TimelineEvent.sequence_no)
        .limit(limit + 1)
    ).all()
    items = [
        EventOut(
            id=e.id,
            sequence_no=e.sequence_no,
            event_type=e.event_type,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            run_id=e.run_id,
            message_id=e.message_id,
            occurred_at=e.occurred_at,
        )
        for e in rows[:limit]
    ]
    next_after = items[-1].sequence_no if len(rows) > limit else None
    return EventPage(items=items, next_after=next_after)


@router.get("/messages/{message_id}/related", response_model=RelatedOut)
def related(message_id: str, session: SessionDep) -> RelatedOut:
    """Resolve a chat message to the graph entities its run created."""
    message = session.get(ChatMessage, message_id)
    if message is None:
        raise ApiError(404, "UNKNOWN_MESSAGE", f"unknown message: {message_id}")
    if message.run_id is None:
        return RelatedOut(message_id=message_id, run_ids=[], entities={}, event_sequences=[])
    # Events are keyed by the run's triggering (question) message.
    question_id = session.scalar(
        select(ChatMessage.id).where(
            ChatMessage.run_id == message.run_id, ChatMessage.role == "user"
        )
    )
    events = session.scalars(
        select(TimelineEvent)
        .where(TimelineEvent.message_id == question_id)
        .order_by(TimelineEvent.sequence_no)
    ).all()
    entities: dict[str, list[str]] = defaultdict(list)
    for event in events:
        if (
            event.entity_type
            and event.entity_id
            and event.entity_id not in entities[event.entity_type]
        ):
            entities[event.entity_type].append(event.entity_id)
    return RelatedOut(
        message_id=message_id,
        run_ids=[message.run_id],
        entities=dict(entities),
        event_sequences=[e.sequence_no for e in events],
    )
