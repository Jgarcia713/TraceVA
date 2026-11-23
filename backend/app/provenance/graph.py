"""Provenance graph computed from relational records (ADR-005: one source of truth).

Edges are derived, never stored, so they cannot drift from the records. Each edge type
has a fixed origin that states its epistemic status:

| Edge          | From -> To                         | Origin                          |
|---------------|------------------------------------|---------------------------------|
| TRIGGERED     | question message -> run            | backend_verified                |
| ANSWERS       | answer message -> question message | backend_verified                |
| CONSUMES      | operation -> view / source         | backend_verified                |
| PRODUCES      | operation -> evidence / view       | backend_verified                |
| DERIVED_FROM  | view -> parent view / source       | backend_verified                |
| SUPPORTS      | evidence -> claim                  | agent_proposed (citation)       |
| QUALIFIES     | assumption -> claim/op/message     | agent_stated / method_template  |

Snapshots: a node's `created_seq` is the sequence number of the timeline event that
created it in this conversation. Nodes created outside the conversation (sources,
root views, views first created elsewhere) have `created_seq = None` and are always
present. Temporal adjacency is never turned into an edge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    Assumption,
    AssumptionLink,
    ChatMessage,
    Claim,
    ClaimEvidence,
    DatasetSource,
    DatasetView,
    Evidence,
    Operation,
    TimelineEvent,
)

BACKEND = "backend_verified"


@dataclass
class Node:
    id: str
    type: str
    label: str
    created_seq: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: str
    origin: str


@dataclass
class Graph:
    run_id: str
    at_sequence: int | None
    nodes: list[Node]
    edges: list[Edge]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "at_sequence": self.at_sequence,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
        }


def _short(value: str, limit: int = 80) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def run_graph(session: Session, run_id: str, at_sequence: int | None = None) -> Graph:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise LookupError(f"unknown run: {run_id}")
    nodes: dict[str, Node] = {}
    edges: set[Edge] = set()

    def add(node: Node) -> None:
        nodes.setdefault(node.id, node)

    add(Node(run.id, "run", f"run ({run.status})", data={"status": run.status}))

    messages = session.scalars(select(ChatMessage).where(ChatMessage.run_id == run_id)).all()
    question = next((m for m in messages if m.role == "user"), None)
    answer = next((m for m in messages if m.role == "assistant"), None)
    for m in messages:
        add(Node(m.id, f"{m.role}_message", _short(m.content), data={"role": m.role}))
    if question:
        edges.add(Edge(question.id, run.id, "TRIGGERED", BACKEND))
    if question and answer:
        edges.add(Edge(answer.id, question.id, "ANSWERS", BACKEND))

    operations = session.scalars(
        select(Operation).where(Operation.run_id == run_id).order_by(Operation.sequence_no)
    ).all()
    view_ids: set[str] = set()
    source_ids: set[str] = set()
    for op in operations:
        add(
            Node(
                op.id,
                "operation",
                f"{op.tool_name} [{op.status}]",
                data={
                    "tool": op.tool_name,
                    "status": op.status,
                    "args": op.args_json,
                    "error": op.error_json,
                },
            )
        )
        for ref in op.input_ids_json:
            edges.add(Edge(op.id, ref, "CONSUMES", BACKEND))
            (view_ids if ref.startswith("view_") else source_ids).add(ref)
        for ref in op.output_ids_json:
            edges.add(Edge(op.id, ref, "PRODUCES", BACKEND))
            view_ids.add(ref)

    evidence_rows = session.scalars(
        select(Evidence).join(Operation).where(Operation.run_id == run_id)
    ).all()
    for ev in evidence_rows:
        add(Node(ev.id, "evidence", f"evidence of {ev.operation_id}"))
        edges.add(Edge(ev.operation_id, ev.id, "PRODUCES", BACKEND))

    # Views and their ancestry back to the source.
    pending = list(view_ids)
    while pending:
        view = session.get(DatasetView, pending.pop())
        if view is None or view.id in nodes:
            continue
        label = "root view" if view.parent_view_id is None else f"view ({view.row_count} rows)"
        add(
            Node(
                view.id,
                "view",
                label,
                data={"filters": view.canonical_filters_json, "row_count": view.row_count},
            )
        )
        if view.parent_view_id:
            edges.add(Edge(view.id, view.parent_view_id, "DERIVED_FROM", BACKEND))
            pending.append(view.parent_view_id)
        else:
            edges.add(Edge(view.id, view.source_id, "DERIVED_FROM", BACKEND))
        source_ids.add(view.source_id)
    for source_id in source_ids:
        source = session.get(DatasetSource, source_id)
        if source is not None:
            add(Node(source.id, "source", source.name, data={"sha256": source.sha256}))

    claims = session.scalars(select(Claim).where(Claim.run_id == run_id)).all()
    for claim in claims:
        add(Node(claim.id, "claim", _short(claim.text), data={"status": claim.status}))
    for link in session.scalars(
        select(ClaimEvidence).where(ClaimEvidence.claim_id.in_([c.id for c in claims]))
    ):
        edges.add(Edge(link.evidence_id, link.claim_id, "SUPPORTS", link.origin))

    assumptions = session.scalars(select(Assumption).where(Assumption.run_id == run_id)).all()
    by_id = {a.id: a for a in assumptions}
    for a in assumptions:
        add(
            Node(
                a.id,
                "assumption",
                _short(a.text),
                data={"source": a.source, "status": a.status, "template_key": a.template_key},
            )
        )
    for qualifies in session.scalars(
        select(AssumptionLink).where(AssumptionLink.assumption_id.in_(list(by_id)))
    ):
        target = qualifies.claim_id or qualifies.operation_id or qualifies.message_id
        assert target is not None  # guaranteed by the CHECK constraint
        origin = by_id[qualifies.assumption_id].source
        edges.add(Edge(qualifies.assumption_id, target, "QUALIFIES", origin))

    # Creation sequence from the conversation's timeline.
    created: dict[str, int] = {}
    for entity_id, seq in session.execute(
        select(TimelineEvent.entity_id, TimelineEvent.sequence_no).where(
            TimelineEvent.conversation_id == run.conversation_id,
            TimelineEvent.entity_id.in_(list(nodes)),
        )
    ):
        if entity_id is not None:
            created[entity_id] = min(seq, created.get(entity_id, seq))
    for node in nodes.values():
        node.created_seq = created.get(node.id)

    kept = {
        node_id: node
        for node_id, node in nodes.items()
        if at_sequence is None or node.created_seq is None or node.created_seq <= at_sequence
    }
    kept_edges = sorted(
        (e for e in edges if e.source in kept and e.target in kept),
        key=lambda e: (e.type, e.source, e.target),
    )
    ordered = sorted(kept.values(), key=lambda n: (n.created_seq or 0, n.type, n.id))
    return Graph(run_id, at_sequence, ordered, kept_edges)


def claim_assumptions(session: Session, claim_id: str) -> dict[str, list[dict[str, Any]]]:
    """Assumptions bearing on a claim.

    - `stated`: agent-stated, with `scope` = "claim" (linked to this claim) or "answer"
      (linked to the run's answer as a whole; it was never tied to this claim).
    - `derived`: method-template caveats of the operations behind the claim's cited
      evidence, and of the operations that produced the views those operations read
      (traversed upstream). Computed from lineage, not stated by the agent. Identical
      texts from several operations are merged, with `operations` listing their sources.
    """
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise LookupError(f"unknown claim: {claim_id}")

    def describe(rows: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": a.id,
                "text": a.text,
                "source": a.source,
                "status": a.status,
                "template_key": a.template_key,
            }
            for a in rows
        ]

    answer_ids = session.scalars(
        select(ChatMessage.id).where(
            ChatMessage.run_id == claim.run_id, ChatMessage.role == "assistant"
        )
    ).all()
    stated: list[dict[str, Any]] = []
    for assumption, link in session.execute(
        select(Assumption, AssumptionLink)
        .join(AssumptionLink, AssumptionLink.assumption_id == Assumption.id)
        .where(
            (AssumptionLink.claim_id == claim_id)
            | (AssumptionLink.message_id.in_(list(answer_ids)))
        )
        .order_by(Assumption.created_at)
    ):
        entry = describe([assumption])[0]
        entry["scope"] = "claim" if link.claim_id == claim_id else "answer"
        stated.append(entry)

    # Upstream traversal: claim -> evidence -> operation -> consumed views -> producers.
    op_ids = set(
        session.scalars(
            select(Evidence.operation_id)
            .join(ClaimEvidence, ClaimEvidence.evidence_id == Evidence.id)
            .where(ClaimEvidence.claim_id == claim_id)
        )
    )
    seen_ops: set[str] = set()
    frontier = set(op_ids)
    while frontier:
        seen_ops |= frontier
        consumed = {
            ref
            for op in session.scalars(select(Operation).where(Operation.id.in_(list(frontier))))
            for ref in op.input_ids_json
            if ref.startswith("view_")
        }
        producers: set[str] = set()
        for view_id in consumed:
            producers |= set(
                session.execute(
                    text(
                        "SELECT operations.id FROM operations, "
                        "json_each(operations.output_ids_json) "
                        "WHERE json_each.value = :view_id AND operations.status = 'succeeded'"
                    ),
                    {"view_id": view_id},
                ).scalars()
            )
        frontier = producers - seen_ops
    merged: dict[str, dict[str, Any]] = {}
    for assumption, link in session.execute(
        select(Assumption, AssumptionLink)
        .join(AssumptionLink, AssumptionLink.assumption_id == Assumption.id)
        .where(AssumptionLink.operation_id.in_(list(seen_ops)))
        .order_by(Assumption.created_at)
    ):
        entry = merged.setdefault(assumption.text, {**describe([assumption])[0], "operations": []})
        entry["operations"].append(link.operation_id)
    return {"stated": stated, "derived": list(merged.values())}
