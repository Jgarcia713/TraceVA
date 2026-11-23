"""Analytical tools: typed input -> typed output, resolving registered IDs to data.

These are plain functions, callable from tests and benchmark scripts without an LLM.
They do NOT record provenance (Operation/Evidence) yet; that arrives in Milestone 3,
which is also why no HTTP endpoint executes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.analysis.datasets import DatasetIntegrityError
from app.analysis.registry import load_source_frame, root_view_id
from app.analysis.statistics import profile_frame, summarize_frame
from app.db.models import DatasetSource, DatasetView
from app.schemas.tools import (
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
    view = ctx.session.get(DatasetView, view_id)
    if view is None:
        raise ToolError(ToolErrorCode.UNKNOWN_VIEW, f"unknown view: {view_id}")
    if view.canonical_filters_json or view.parent_view_id is not None:
        # Filtered/derived views arrive with create_view; refuse rather than misapply.
        raise ToolError(
            ToolErrorCode.UNSUPPORTED_VIEW,
            "only root views are supported in this version",
            {"view_id": view_id},
        )
    source = ctx.session.get(DatasetSource, view.source_id)
    assert source is not None  # guaranteed by the foreign key
    frame = _load_source(ctx, source)
    return frame[view.columns_json]


def inspect_dataset(ctx: ToolContext, args: InspectDatasetInput) -> InspectDatasetOutput:
    source = ctx.session.get(DatasetSource, args.source_id)
    if source is None:
        raise ToolError(ToolErrorCode.UNKNOWN_SOURCE, f"unknown source: {args.source_id}")
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
