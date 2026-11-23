"""HTTP request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


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
