"""Runs, their provenance graph, and evidence."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.api.errors import ApiError
from app.api.views import run_detail
from app.db.models import AnalysisRun, Evidence, Operation
from app.provenance.graph import run_graph
from app.schemas.api import EvidenceOut, GraphOut, RunDetail

router = APIRouter(tags=["runs"])


def _run(session: SessionDep, run_id: str) -> AnalysisRun:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise ApiError(404, "UNKNOWN_RUN", f"unknown run: {run_id}")
    return run


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, session: SessionDep) -> RunDetail:
    """Run status (poll this after posting a message), claims, evidence, assumptions."""
    return run_detail(session, _run(session, run_id))


@router.get("/runs/{run_id}/graph", response_model=GraphOut)
def get_graph(
    run_id: str,
    session: SessionDep,
    at_sequence: int | None = Query(
        None, ge=0, description="Snapshot: only nodes created at or before this event."
    ),
) -> GraphOut:
    _run(session, run_id)
    return GraphOut.model_validate(run_graph(session, run_id, at_sequence).to_dict())


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
def get_evidence(evidence_id: str, session: SessionDep) -> EvidenceOut:
    evidence = session.get(Evidence, evidence_id)
    if evidence is None:
        raise ApiError(404, "UNKNOWN_EVIDENCE", f"unknown evidence: {evidence_id}")
    operation = session.get(Operation, evidence.operation_id)
    assert operation is not None
    return EvidenceOut(
        id=evidence.id,
        operation_id=operation.id,
        run_id=operation.run_id,
        tool_name=operation.tool_name,
        result=evidence.result_json,
        created_at=evidence.created_at,
    )
