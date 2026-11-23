"""Analytical tools: typed input -> typed output, resolving registered IDs to data.

These are plain functions, callable from tests and benchmark scripts without an LLM.
Called directly they record nothing; the agent orchestrator (app/agent) wraps every
model-initiated call in an Operation and stores successful results as Evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.datasets import DatasetIntegrityError
from app.analysis.registry import load_source_frame, root_view_id
from app.analysis.statistics import profile_frame, summarize_frame
from app.analysis.views import (
    CHILD_NULL_POLICY,
    apply_filters,
    canonical_filter,
    combine_filters,
    definition_sha256,
    rows_excluded_missing,
    validate_filters,
    view_warnings,
)
from app.db.base import new_id
from app.db.models import DatasetSource, DatasetView
from app.schemas.tools import (
    CreateViewInput,
    CreateViewOutput,
    FilterReport,
    InspectDatasetInput,
    InspectDatasetOutput,
    SummarizeInput,
    SummarizeOutput,
    ToolError,
    ToolErrorCode,
)


@dataclass(frozen=True)
class ToolContext:
    session: Session
    data_dir: Path
    max_result_rows: int


@dataclass(frozen=True)
class ViewDraft:
    """A new view computed by a tool. Tools never write; the caller persists drafts
    (the orchestrator does so in the same transaction as the Operation)."""

    id: str
    source_id: str
    parent_view_id: str
    canonical_filters_json: list[dict]
    columns_json: list[str]
    null_policy: str
    row_count: int
    definition_sha256: str


@dataclass(frozen=True)
class ToolResult:
    """Tool output plus side effects for the caller to persist."""

    output: BaseModel
    new_views: list[ViewDraft] = field(default_factory=list)
    output_ids: list[str] = field(default_factory=list)


def persist_views(session: Session, drafts: list[ViewDraft]) -> None:
    for draft in drafts:
        session.add(DatasetView(**draft.__dict__))
    session.flush()


def _load_source(ctx: ToolContext, source: DatasetSource) -> pd.DataFrame:
    try:
        return load_source_frame(source, ctx.data_dir)
    except (DatasetIntegrityError, KeyError) as exc:
        raise ToolError(
            ToolErrorCode.DATASET_INTEGRITY,
            "registered dataset could not be loaded or failed verification",
            {"source_id": source.id, "reason": str(exc)},
        ) from exc


def load_view_frame(ctx: ToolContext, view_id: str) -> pd.DataFrame:
    view = _get_view(ctx, view_id)
    source = ctx.session.get(DatasetSource, view.source_id)
    assert source is not None  # guaranteed by the foreign key
    frame = _load_source(ctx, source)
    return _apply_view(frame, view)


def _known_ids(ctx: ToolContext) -> list[dict[str, str]]:
    """Valid starting points, so an unknown-ID error tells the model what to use instead."""
    rows = ctx.session.execute(
        select(DatasetSource.name, DatasetSource.id, DatasetView.id)
        .join(DatasetView, DatasetView.source_id == DatasetSource.id)
        .where(DatasetView.parent_view_id.is_(None))
        .order_by(DatasetSource.created_at)
    ).all()
    return [{"name": n, "source_id": s, "root_view_id": v} for n, s, v in rows]


def _get_view(ctx: ToolContext, view_id: str) -> DatasetView:
    view = ctx.session.get(DatasetView, view_id)
    if view is None:
        raise ToolError(
            ToolErrorCode.UNKNOWN_VIEW,
            f"unknown view: {view_id}. Use a view_id from the dataset list or one returned "
            "by create_view; IDs cannot be guessed.",
            {"registered_datasets": _known_ids(ctx)},
        )
    return view


def _apply_view(source_frame: pd.DataFrame, view: DatasetView) -> pd.DataFrame:
    # A view stores its complete filter set, so it is applied directly to the source.
    return apply_filters(source_frame, view.canonical_filters_json)[view.columns_json]


def create_view(ctx: ToolContext, args: CreateViewInput) -> ToolResult:
    base = _get_view(ctx, args.view_id)
    source = ctx.session.get(DatasetSource, base.source_id)
    assert source is not None
    source_frame = _load_source(ctx, source)
    base_frame = _apply_view(source_frame, base)
    validate_filters(base_frame, args.filters)

    filters = combine_filters(base.canonical_filters_json, args.filters)
    columns = list(base.columns_json)
    digest = definition_sha256(source.id, filters, columns, CHILD_NULL_POLICY)
    view_frame = apply_filters(source_frame, filters)[columns]
    existing = ctx.session.scalars(
        select(DatasetView).where(
            DatasetView.source_id == source.id, DatasetView.definition_sha256 == digest
        )
    ).one_or_none()

    drafts: list[ViewDraft] = []
    if existing is not None:
        view_id = existing.id
    else:
        view_id = new_id("view")
        drafts.append(
            ViewDraft(
                id=view_id,
                source_id=source.id,
                parent_view_id=base.id,
                canonical_filters_json=filters,
                columns_json=columns,
                null_policy=CHILD_NULL_POLICY,
                row_count=len(view_frame),
                definition_sha256=digest,
            )
        )
    output = CreateViewOutput(
        view_id=view_id,
        based_on_view_id=base.id,
        source_id=source.id,
        reused=existing is not None,
        base_row_count=len(base_frame),
        row_count=len(view_frame),
        filters=filters,
        applied=[
            FilterReport(
                **canonical_filter(f), rows_excluded_missing=rows_excluded_missing(base_frame, f)
            )
            for f in args.filters
        ],
        warnings=view_warnings(len(view_frame)),
    )
    return ToolResult(output=output, new_views=drafts, output_ids=[view_id])


def inspect_dataset(ctx: ToolContext, args: InspectDatasetInput) -> InspectDatasetOutput:
    source = ctx.session.get(DatasetSource, args.source_id)
    if source is None:
        raise ToolError(
            ToolErrorCode.UNKNOWN_SOURCE,
            f"unknown source: {args.source_id}",
            {"registered_datasets": _known_ids(ctx)},
        )
    frame = _load_source(ctx, source)
    return InspectDatasetOutput(
        source_id=source.id,
        name=source.name,
        root_view_id=root_view_id(ctx.session, source.id),
        sha256=source.sha256,
        parser_version=source.parser_version,
        row_count=len(frame),
        columns=profile_frame(frame),
    )


def summarize(ctx: ToolContext, args: SummarizeInput) -> SummarizeOutput:
    frame = load_view_frame(ctx, args.view_id)
    groups, total_groups, truncated, warnings = summarize_frame(
        frame, args.columns, args.group_by, args.metrics, ctx.max_result_rows
    )
    return SummarizeOutput(
        view_id=args.view_id,
        row_count=len(frame),
        group_by=args.group_by,
        metrics=args.metrics,
        total_groups=total_groups,
        truncated=truncated,
        groups=groups,
        warnings=warnings,
    )
