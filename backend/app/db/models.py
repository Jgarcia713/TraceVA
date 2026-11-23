"""ORM models. Every table added here needs a reviewed Alembic migration (ADR-002).

Foreign keys deliberately have no ON DELETE CASCADE: provenance-related records are
append-only, and a cascade would turn an accidental delete into silent data loss.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime, new_id, utcnow


class DatasetSource(Base):
    """A registered, immutable input file (identified by content hash + parser version)."""

    __tablename__ = "dataset_sources"
    __table_args__ = (UniqueConstraint("name", "sha256", "parser_version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("src"))
    name: Mapped[str] = mapped_column(String(100))
    file_path: Mapped[str] = mapped_column(String(500))  # relative to DATA_DIR
    sha256: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(50))
    schema_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DatasetView(Base):
    """A reproducible selection/projection of a source.

    Each source has exactly one root view (no parent, no filters, all columns), created
    at registration. Child views (create_view) store their *complete* canonical filter
    set and are identified by `definition_sha256`, so identical definitions are reused.
    `parent_view_id` records the base view of the call that first created the view.
    """

    __tablename__ = "dataset_views"
    __table_args__ = (
        Index(
            "uq_dataset_views_root_per_source",
            "source_id",
            unique=True,
            sqlite_where=text("parent_view_id IS NULL"),
        ),
        Index(
            "uq_dataset_views_definition",
            "source_id",
            "definition_sha256",
            unique=True,
            sqlite_where=text("definition_sha256 IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("view"))
    source_id: Mapped[str] = mapped_column(ForeignKey("dataset_sources.id"), index=True)
    parent_view_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_views.id"))
    canonical_filters_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    columns_json: Mapped[list[str]] = mapped_column(JSON)
    null_policy: Mapped[str] = mapped_column(String(30))
    row_count: Mapped[int] = mapped_column(Integer)
    # Null for root views (identified by the root index); set for every child view.
    definition_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Milestone 2: conversations, runs, and the execution record
# ---------------------------------------------------------------------------


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("conv"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AnalysisRun(Base):
    """One investigation. Status follows running -> completed | failed; nothing else mutates.

    The triggering question is the user ChatMessage with this run_id (one per run).
    `parent_run_id` is reserved for challenge branches (Milestone 4).
    """

    __tablename__ = "analysis_runs"
    __table_args__ = (
        # At most one running run per conversation, enforced by the database so that
        # concurrent requests cannot race past an application-level check.
        Index(
            "uq_analysis_runs_one_running_per_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("run"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"))
    status: Mapped[str] = mapped_column(String(20))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON)  # model/prompt snapshot, no secrets
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_no"),
        Index(
            "uq_chat_messages_one_question_per_run",
            "run_id",
            unique=True,
            sqlite_where=text("role = 'user' AND run_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("msg"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    sequence_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ModelCall(Base):
    """One model request/response. Stores model-visible output only: reasoning text is
    discarded by the client and only its length is recorded (brief invariant 8)."""

    __tablename__ = "model_calls"
    __table_args__ = (UniqueConstraint("run_id", "step_no"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mc"))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    step_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)  # raw, as received
    finish_reason: Mapped[str | None] = mapped_column(String(40))
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency_ms: Mapped[int] = mapped_column(Integer)
    reasoning_chars_discarded: Mapped[int] = mapped_column(Integer, default=0)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Operation(Base):
    """An actual backend tool invocation, successful or not. Written once, never updated."""

    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("run_id", "sequence_no"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("op"))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    model_call_id: Mapped[str | None] = mapped_column(ForeignKey("model_calls.id"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    tool_call_id: Mapped[str | None] = mapped_column(String(100))  # provider-assigned
    tool_name: Mapped[str] = mapped_column(String(100))
    raw_arguments: Mapped[str] = mapped_column(Text)  # exactly as the model sent them
    args_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # validated; null if invalid
    input_ids_json: Mapped[list[str]] = mapped_column(JSON)  # source/view IDs consumed
    # IDs of artifacts the operation produced or resolved (e.g. a view), besides evidence.
    output_ids_json: Mapped[list[str]] = mapped_column(JSON, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Evidence(Base):
    """Immutable computed result of exactly one successful operation."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ev"))
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"), unique=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Claim(Base):
    """Model-authored interpretation. Citations are agent-proposed, not proof."""

    __tablename__ = "claims"
    __table_args__ = (UniqueConstraint("run_id", "sequence_no"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("clm"))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # proposed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"), primary_key=True)
    origin: Mapped[str] = mapped_column(String(30))  # agent_proposed


# ---------------------------------------------------------------------------
# Milestone 3: assumptions and timeline
# ---------------------------------------------------------------------------


class Assumption(Base):
    """A stated assumption or caveat. Never marked as checked unless it actually was
    (brief invariant 7); every current source yields status 'not_tested'."""

    __tablename__ = "assumptions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("asm"))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30))  # agent_stated | method_template
    template_key: Mapped[str | None] = mapped_column(String(60))  # method_template only
    status: Mapped[str] = mapped_column(String(20))  # not_tested
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AssumptionLink(Base):
    """QUALIFIES relation: an assumption qualifies exactly one claim, operation, or answer
    message. Typed foreign keys + CHECK make endpoint validity a database guarantee."""

    __tablename__ = "assumption_links"
    __table_args__ = (
        CheckConstraint(
            "(claim_id IS NOT NULL) + (operation_id IS NOT NULL) + (message_id IS NOT NULL) = 1",
            name="exactly_one_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("asl"))
    assumption_id: Mapped[str] = mapped_column(ForeignKey("assumptions.id"), index=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), index=True)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("operations.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("chat_messages.id"), index=True)


class TimelineEvent(Base):
    """Ordered record of what happened, for UI synchronization and replay. Written in the
    same transaction as the entity it describes. Temporal order is not dependency: the
    provenance graph holds dependencies (brief invariant 4)."""

    __tablename__ = "timeline_events"
    __table_args__ = (UniqueConstraint("conversation_id", "sequence_no"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("evt"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("chat_messages.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str | None] = mapped_column(String(30))
    entity_id: Mapped[str | None] = mapped_column(String(40), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
