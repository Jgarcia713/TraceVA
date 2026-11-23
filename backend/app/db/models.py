"""ORM models. Every table added here needs a reviewed Alembic migration (ADR-002).

Foreign keys deliberately have no ON DELETE CASCADE: provenance-related records are
append-only, and a cascade would turn an accidental delete into silent data loss.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint, text
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
    at registration. Later views (create_view) are children of an existing view.
    """

    __tablename__ = "dataset_views"
    __table_args__ = (
        Index(
            "uq_dataset_views_root_per_source",
            "source_id",
            unique=True,
            sqlite_where=text("parent_view_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("view"))
    source_id: Mapped[str] = mapped_column(ForeignKey("dataset_sources.id"), index=True)
    parent_view_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_views.id"))
    canonical_filters_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    columns_json: Mapped[list[str]] = mapped_column(JSON)
    null_policy: Mapped[str] = mapped_column(String(30))
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
