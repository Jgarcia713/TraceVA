"""create_view support: view definition hashes and operation outputs.

Revision ID: 0003_views
Revises: 0002_runs
Create Date: 2026-11-23

- dataset_views.definition_sha256 identifies child views by definition (reuse); it stays
  NULL for existing root views, which are identified by the root-per-source index.
  Nothing is backfilled.
- operations.output_ids_json defaults to [] for existing rows. That is accurate rather
  than invented: operations before create_view produced no artifacts besides evidence.
- The downgrade drops columns, which makes Alembic rebuild `operations` (referenced by
  `evidence`); the ADR-002 procedure (FKs off, foreign_key_check before commit) applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_views"
down_revision: str | None = "0002_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("dataset_views", schema=None) as batch_op:
        batch_op.add_column(sa.Column("definition_sha256", sa.String(length=64), nullable=True))
        batch_op.create_index(
            "uq_dataset_views_definition",
            ["source_id", "definition_sha256"],
            unique=True,
            sqlite_where=sa.text("definition_sha256 IS NOT NULL"),
        )

    with op.batch_alter_table("operations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("output_ids_json", sa.JSON(), server_default=sa.text("'[]'"), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("operations", schema=None) as batch_op:
        batch_op.drop_column("output_ids_json")

    with op.batch_alter_table("dataset_views", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_dataset_views_definition", sqlite_where=sa.text("definition_sha256 IS NOT NULL")
        )
        batch_op.drop_column("definition_sha256")
