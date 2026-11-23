"""Dataset registry: sources and views.

Revision ID: 0001_datasets
Revises: 0000_baseline
Create Date: 2026-11-22

Timestamps use plain DateTime (naive UTC); app.db.base.UTCDateTime handles conversion.
Migrations intentionally do not import app types so they remain stable as code evolves.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_datasets"
down_revision: str | None = "0000_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_sources",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_sources")),
        sa.UniqueConstraint(
            "name",
            "sha256",
            "parser_version",
            name=op.f("uq_dataset_sources_name_sha256_parser_version"),
        ),
    )
    op.create_table(
        "dataset_views",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=40), nullable=False),
        sa.Column("parent_view_id", sa.String(length=40), nullable=True),
        sa.Column("canonical_filters_json", sa.JSON(), nullable=False),
        sa.Column("columns_json", sa.JSON(), nullable=False),
        sa.Column("null_policy", sa.String(length=30), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_view_id"],
            ["dataset_views.id"],
            name=op.f("fk_dataset_views_parent_view_id_dataset_views"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["dataset_sources.id"],
            name=op.f("fk_dataset_views_source_id_dataset_sources"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_views")),
    )
    with op.batch_alter_table("dataset_views") as batch_op:
        batch_op.create_index(batch_op.f("ix_dataset_views_source_id"), ["source_id"])
        # One root view per source (partial unique index).
        batch_op.create_index(
            "uq_dataset_views_root_per_source",
            ["source_id"],
            unique=True,
            sqlite_where=sa.text("parent_view_id IS NULL"),
        )


def downgrade() -> None:
    with op.batch_alter_table("dataset_views") as batch_op:
        batch_op.drop_index("uq_dataset_views_root_per_source")
        batch_op.drop_index(batch_op.f("ix_dataset_views_source_id"))
    op.drop_table("dataset_views")
    op.drop_table("dataset_sources")
