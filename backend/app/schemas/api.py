"""HTTP request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    schema_revision: str | None
    schema_head: str | None
    schema_up_to_date: bool


class DatasetColumn(BaseModel):
    name: str
    kind: Literal["numeric", "categorical"]
    dtype: str


class DatasetSummary(BaseModel):
    id: str
    name: str
    sha256: str
    parser_version: str
    row_count: int
    created_at: datetime
    root_view_id: str
    columns: list[DatasetColumn]


# ---------------------------------------------------------------------------
# M3b: conversations, runs, provenance
# ---------------------------------------------------------------------------


class ConversationOut(BaseModel):
    id: str
    created_at: datetime
    active_run_id: str | None


class PostMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    run_id: str | None
    role: Literal["user", "assistant"]
    content: str
    sequence_no: int
    created_at: datetime
    # Assistant explanations are model-written and not verified; only claims are cited.
    verified: bool | None = None
    unverified_numbers: list[str] | None = None


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_after: int | None = Field(description="Pass as `after` for the next page.")


class RunLinks(BaseModel):
    run: str
    graph: str
    timeline: str


class RunAccepted(BaseModel):
    run_id: str
    conversation_id: str
    message_id: str
    status: Literal["running"]
    links: RunLinks


class AssumptionOut(BaseModel):
    id: str
    text: str
    source: Literal["agent_stated", "method_template"]
    status: str
    template_key: str | None = None
    scope: Literal["claim", "answer"] | None = None
    operations: list[str] | None = None


class ClaimAssumptions(BaseModel):
    stated: list[AssumptionOut]
    derived: list[AssumptionOut]


class ClaimOut(BaseModel):
    id: str
    sequence_no: int
    text: str
    status: str
    evidence_ids: list[str]
    citation_origin: Literal["agent_proposed"] = "agent_proposed"
    assumptions: ClaimAssumptions


class OperationOut(BaseModel):
    id: str
    sequence_no: int
    tool_name: str
    status: Literal["succeeded", "failed"]
    raw_arguments: str
    args: dict[str, Any] | None
    error: dict[str, Any] | None
    input_ids: list[str]
    output_ids: list[str]
    evidence_id: str | None
    started_at: datetime
    completed_at: datetime


class RunDetail(BaseModel):
    id: str
    conversation_id: str
    parent_run_id: str | None
    status: Literal["running", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None
    error: dict[str, Any] | None
    usage: dict[str, Any] | None
    config: dict[str, Any]
    question: MessageOut | None
    answer: MessageOut | None
    operations: list[OperationOut]
    claims: list[ClaimOut]
    assumptions: list[AssumptionOut]
    last_event_sequence: int | None


class EvidenceOut(BaseModel):
    id: str
    operation_id: str
    run_id: str
    tool_name: str
    result: dict[str, Any]
    created_at: datetime


class GraphNodeOut(BaseModel):
    id: str
    type: str
    label: str
    created_seq: int | None
    data: dict[str, Any]


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    type: str
    origin: str


class GraphOut(BaseModel):
    run_id: str
    at_sequence: int | None
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class EventOut(BaseModel):
    id: str
    sequence_no: int
    event_type: str
    entity_type: str | None
    entity_id: str | None
    run_id: str | None
    message_id: str | None
    occurred_at: datetime


class EventPage(BaseModel):
    items: list[EventOut]
    next_after: int | None


class RelatedOut(BaseModel):
    message_id: str
    run_ids: list[str]
    entities: dict[str, list[str]] = Field(description="Entity IDs grouped by type.")
    event_sequences: list[int]
