"""Read-only access to registered datasets (metadata only; no tool execution)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.registry import root_view_id
from app.api.errors import ApiError
from app.db.models import DatasetSource
from app.schemas.api import DatasetColumn, DatasetSummary

router = APIRouter(prefix="/datasets", tags=["datasets"])


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def _summary(session: Session, source: DatasetSource) -> DatasetSummary:
    return DatasetSummary(
        id=source.id,
        name=source.name,
        sha256=source.sha256,
        parser_version=source.parser_version,
        row_count=source.row_count,
        created_at=source.created_at,
        root_view_id=root_view_id(session, source.id),
        columns=[DatasetColumn.model_validate(c) for c in source.schema_json],
    )


@router.get("", response_model=list[DatasetSummary])
def list_datasets(session: SessionDep) -> list[DatasetSummary]:
    sources = session.scalars(select(DatasetSource).order_by(DatasetSource.created_at)).all()
    return [_summary(session, s) for s in sources]


@router.get("/{source_id}", response_model=DatasetSummary)
def get_dataset(source_id: str, session: SessionDep) -> DatasetSummary:
    source = session.get(DatasetSource, source_id)
    if source is None:
        raise ApiError(404, "UNKNOWN_SOURCE", f"unknown dataset source: {source_id}")
    return _summary(session, source)
