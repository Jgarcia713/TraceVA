"""Code-defined registry of known datasets, and their registration into the database.

The model (and API callers) can only refer to datasets by registered ID; file paths
come exclusively from this module, never from user or model input.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.datasets import (
    ADULT_PARSER_VERSION,
    Parser,
    column_kind,
    load_frame,
    parse_adult,
    resolve_data_path,
)
from app.db.models import DatasetSource, DatasetView

ROOT_NULL_POLICY = "keep"  # root views keep all rows; tools report missingness explicitly


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    relative_path: str  # relative to DATA_DIR
    sha256: str
    parser_version: str
    parser: Parser
    description: str


DATASETS: dict[str, DatasetSpec] = {
    spec.name: spec
    for spec in [
        DatasetSpec(
            name="adult",
            relative_path="adult/adult.data",
            sha256="5b00264637dbfec36bdeaab5676b0b309ff9eb788d63554ca0a249491c86603d",
            parser_version=ADULT_PARSER_VERSION,
            parser=parse_adult,
            description="UCI Adult Income, training split (adult.data), 32,561 rows.",
        ),
        DatasetSpec(
            name="adult_sample",
            relative_path="adult_sample.data",
            sha256="7e87fe36d36dd1e4df2d7cca00dd4a9716ee32d3c05d6bc2356fc717fc8a779e",
            parser_version=ADULT_PARSER_VERSION,
            parser=parse_adult,
            description="50-row test fixture from adult.data (lives in tests/fixtures).",
        ),
    ]
}

PARSERS: dict[str, Parser] = {ADULT_PARSER_VERSION: parse_adult}


class UnknownDatasetError(KeyError):
    pass


def frame_schema(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {"name": str(col), "kind": column_kind(frame[col]), "dtype": str(frame[col].dtype)}
        for col in frame.columns
    ]


def register_dataset(
    session: Session, name: str, data_dir: Path, *, spec: DatasetSpec | None = None
) -> DatasetSource:
    """Register a known dataset (idempotent) and create its root view.

    Returns the existing source if (name, sha256, parser_version) is already registered.
    Raises DatasetIntegrityError if the file is missing, escapes DATA_DIR, or its hash
    does not match the pinned value. `spec` overrides the registry lookup (tests only).
    """
    if spec is None:
        if name not in DATASETS:
            raise UnknownDatasetError(name)
        spec = DATASETS[name]

    path = resolve_data_path(data_dir, spec.relative_path)
    existing = session.scalars(
        select(DatasetSource).where(
            DatasetSource.name == spec.name,
            DatasetSource.sha256 == spec.sha256,
            DatasetSource.parser_version == spec.parser_version,
        )
    ).one_or_none()

    frame = load_frame(path, spec.sha256, spec.parser_version, spec.parser)
    if existing is not None:
        return existing

    source = DatasetSource(
        name=spec.name,
        file_path=spec.relative_path,
        sha256=spec.sha256,
        parser_version=spec.parser_version,
        schema_json=frame_schema(frame),
        row_count=len(frame),
    )
    session.add(source)
    session.flush()
    session.add(
        DatasetView(
            source_id=source.id,
            parent_view_id=None,
            canonical_filters_json=[],
            columns_json=[str(c) for c in frame.columns],
            null_policy=ROOT_NULL_POLICY,
            row_count=len(frame),
        )
    )
    session.flush()
    return source


def root_view_id(session: Session, source_id: str) -> str:
    view_id = session.scalars(
        select(DatasetView.id).where(
            DatasetView.source_id == source_id, DatasetView.parent_view_id.is_(None)
        )
    ).one_or_none()
    if view_id is None:
        raise LookupError(f"source {source_id} has no root view")
    return view_id


def load_source_frame(source: DatasetSource, data_dir: Path) -> pd.DataFrame:
    """Load a registered source's frame, re-verifying the stored hash on first load."""
    parser = PARSERS[source.parser_version]
    path = resolve_data_path(data_dir, source.file_path)
    return load_frame(path, source.sha256, source.parser_version, parser)
