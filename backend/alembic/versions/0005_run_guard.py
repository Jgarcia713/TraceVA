"""At most one running run per conversation (M3b).

Revision ID: 0005_run_guard
Revises: 0004_provenance
Create Date: 2026-11-23

Partial unique index on analysis_runs(conversation_id) WHERE status = 'running'. If a
database already holds two running runs in one conversation (e.g. after a crash), this
migration fails loudly; start the app once (it marks interrupted runs as failed) or fix
the rows explicitly. Historical runs are never rewritten by the migration itself.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_run_guard"
down_revision: str | None = "0004_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.create_index(
            "uq_analysis_runs_one_running_per_conversation",
            ["conversation_id"],
            unique=True,
            sqlite_where=sa.text("status = 'running'"),
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_analysis_runs_one_running_per_conversation",
            sqlite_where=sa.text("status = 'running'"),
        )
