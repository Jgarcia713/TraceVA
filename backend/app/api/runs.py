"""Runs, their provenance graph, and evidence."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.analysis.charts import ChartSpec, chart_for_evidence, claim_charts, describe_filters
from app.api.deps import SessionDep
from app.api.errors import ApiError
from app.api.views import run_detail
from app.db.models import AnalysisRun, Claim, ClaimEvidence, DatasetView, Evidence, Operation
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


@router.get("/evidence/{evidence_id}/chart", response_model=ChartSpec)
def get_evidence_chart(evidence_id: str, session: SessionDep) -> ChartSpec:
    """Deterministic chart spec derived from the evidence (backend-owned, brief §7)."""
    evidence = session.get(Evidence, evidence_id)
    if evidence is None:
        raise ApiError(404, "UNKNOWN_EVIDENCE", f"unknown evidence: {evidence_id}")
    operation = session.get(Operation, evidence.operation_id)
    assert operation is not None
    spec = chart_for_evidence(operation.tool_name, evidence.id, evidence.result_json)
    if spec is None:
        raise ApiError(404, "NO_CHART", f"no chart for {operation.tool_name} evidence")
    return spec


class ClaimCharts(BaseModel):
    claim_id: str
    charts: list[ChartSpec]


@router.get("/claims/{claim_id}/charts", response_model=ClaimCharts)
def get_claim_charts(claim_id: str, session: SessionDep) -> ClaimCharts:
    """Charts of the evidence a claim cites; single-value charts of one measure are merged
    into a comparison labelled by each evidence's view definition."""
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise ApiError(404, "UNKNOWN_CLAIM", f"unknown claim: {claim_id}")
    cited: list[tuple[ChartSpec, str]] = []
    for evidence in session.scalars(
        select(Evidence)
        .join(ClaimEvidence, ClaimEvidence.evidence_id == Evidence.id)
        .where(ClaimEvidence.claim_id == claim_id)
        .order_by(Evidence.created_at)
    ):
        operation = session.get(Operation, evidence.operation_id)
        assert operation is not None
        spec = chart_for_evidence(operation.tool_name, evidence.id, evidence.result_json)
        if spec is None:
            continue
        view_id = (operation.args_json or {}).get("view_id")
        view = session.get(DatasetView, view_id) if view_id else None
        label = describe_filters(view.canonical_filters_json) if view else "all rows"
        cited.append((spec, label))
    return ClaimCharts(claim_id=claim_id, charts=claim_charts(cited))


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
