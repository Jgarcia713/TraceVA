"""Provenance (M3a): assumptions, assumption links, timeline events.

Revision ID: 0004_provenance
Revises: 0003_views
Create Date: 2026-11-23

- assumption_links uses typed nullable FKs plus a CHECK that exactly one target is set,
  so QUALIFIES endpoints are valid by construction (ADR-005).
- timeline_events.entity_id is polymorphic (no FK); emission happens in the same
  transaction as the entity, and an integrity test resolves every entity_id.
- New tables only: nothing is backfilled for runs recorded before this revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_provenance"
down_revision: str | None = "0003_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assumptions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("template_key", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], name=op.f("fk_assumptions_run_id_analysis_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assumptions")),
    )
    with op.batch_alter_table("assumptions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assumptions_run_id"), ["run_id"], unique=False)

    op.create_table(
        "timeline_events",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=True),
        sa.Column("message_id", sa.String(length=40), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=True),
        sa.Column("entity_id", sa.String(length=40), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_timeline_events_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("fk_timeline_events_message_id_chat_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], name=op.f("fk_timeline_events_run_id_analysis_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timeline_events")),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name=op.f("uq_timeline_events_conversation_id_sequence_no"),
        ),
    )
    with op.batch_alter_table("timeline_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_timeline_events_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_timeline_events_entity_id"), ["entity_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_timeline_events_run_id"), ["run_id"], unique=False)

    op.create_table(
        "assumption_links",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("assumption_id", sa.String(length=40), nullable=False),
        sa.Column("claim_id", sa.String(length=40), nullable=True),
        sa.Column("operation_id", sa.String(length=40), nullable=True),
        sa.Column("message_id", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "(claim_id IS NOT NULL) + (operation_id IS NOT NULL) + (message_id IS NOT NULL) = 1",
            name=op.f("ck_assumption_links_exactly_one_target"),
        ),
        sa.ForeignKeyConstraint(
            ["assumption_id"],
            ["assumptions.id"],
            name=op.f("fk_assumption_links_assumption_id_assumptions"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], name=op.f("fk_assumption_links_claim_id_claims")
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("fk_assumption_links_message_id_chat_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
            name=op.f("fk_assumption_links_operation_id_operations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assumption_links")),
    )
    with op.batch_alter_table("assumption_links", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_assumption_links_assumption_id"), ["assumption_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_assumption_links_claim_id"), ["claim_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_assumption_links_message_id"), ["message_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_assumption_links_operation_id"), ["operation_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("assumption_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assumption_links_operation_id"))
        batch_op.drop_index(batch_op.f("ix_assumption_links_message_id"))
        batch_op.drop_index(batch_op.f("ix_assumption_links_claim_id"))
        batch_op.drop_index(batch_op.f("ix_assumption_links_assumption_id"))

    op.drop_table("assumption_links")
    with op.batch_alter_table("timeline_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_timeline_events_run_id"))
        batch_op.drop_index(batch_op.f("ix_timeline_events_entity_id"))
        batch_op.drop_index(batch_op.f("ix_timeline_events_conversation_id"))

    op.drop_table("timeline_events")
    with op.batch_alter_table("assumptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assumptions_run_id"))

    op.drop_table("assumptions")
