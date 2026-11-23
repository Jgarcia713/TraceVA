"""Runs and execution record: conversations, messages, runs, model calls, operations,
evidence, claims, and claim-evidence links (Milestone 2).

Revision ID: 0002_runs
Revises: 0001_datasets
Create Date: 2026-11-22

No ON DELETE CASCADE anywhere: provenance records are append-only (ADR-002).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_runs"
down_revision: str | None = "0001_datasets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.String(length=40), nullable=False),
        sa.Column("parent_run_id", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_analysis_runs_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_analysis_runs_parent_run_id_analysis_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs")),
    )
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_analysis_runs_conversation_id"), ["conversation_id"], unique=False
        )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_chat_messages_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], name=op.f("fk_chat_messages_run_id_analysis_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name=op.f("uq_chat_messages_conversation_id_sequence_no"),
        ),
    )
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chat_messages_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_chat_messages_run_id"), ["run_id"], unique=False)
        batch_op.create_index(
            "uq_chat_messages_one_question_per_run",
            ["run_id"],
            unique=True,
            sqlite_where=sa.text("role = 'user' AND run_id IS NOT NULL"),
        )

    op.create_table(
        "claims",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], name=op.f("fk_claims_run_id_analysis_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
        sa.UniqueConstraint("run_id", "sequence_no", name=op.f("uq_claims_run_id_sequence_no")),
    )
    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_claims_run_id"), ["run_id"], unique=False)

    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls_json", sa.JSON(), nullable=False),
        sa.Column("finish_reason", sa.String(length=40), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("reasoning_chars_discarded", sa.Integer(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], name=op.f("fk_model_calls_run_id_analysis_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_calls")),
        sa.UniqueConstraint("run_id", "step_no", name=op.f("uq_model_calls_run_id_step_no")),
    )
    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_model_calls_run_id"), ["run_id"], unique=False)

    op.create_table(
        "operations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("model_call_id", sa.String(length=40), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=100), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("raw_arguments", sa.Text(), nullable=False),
        sa.Column("args_json", sa.JSON(), nullable=True),
        sa.Column("input_ids_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["model_calls.id"],
            name=op.f("fk_operations_model_call_id_model_calls"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["analysis_runs.id"], name=op.f("fk_operations_run_id_analysis_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operations")),
        sa.UniqueConstraint("run_id", "sequence_no", name=op.f("uq_operations_run_id_sequence_no")),
    )
    with op.batch_alter_table("operations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_operations_run_id"), ["run_id"], unique=False)

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("operation_id", sa.String(length=40), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["operations.id"], name=op.f("fk_evidence_operation_id_operations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_evidence_operation_id")),
    )
    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.String(length=40), nullable=False),
        sa.Column("evidence_id", sa.String(length=40), nullable=False),
        sa.Column("origin", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], name=op.f("fk_claim_evidence_claim_id_claims")
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name=op.f("fk_claim_evidence_evidence_id_evidence")
        ),
        sa.PrimaryKeyConstraint("claim_id", "evidence_id", name=op.f("pk_claim_evidence")),
    )


def downgrade() -> None:
    op.drop_table("claim_evidence")
    op.drop_table("evidence")
    with op.batch_alter_table("operations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_operations_run_id"))

    op.drop_table("operations")
    with op.batch_alter_table("model_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_calls_run_id"))

    op.drop_table("model_calls")
    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_claims_run_id"))

    op.drop_table("claims")
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_chat_messages_one_question_per_run",
            sqlite_where=sa.text("role = 'user' AND run_id IS NOT NULL"),
        )
        batch_op.drop_index(batch_op.f("ix_chat_messages_run_id"))
        batch_op.drop_index(batch_op.f("ix_chat_messages_conversation_id"))

    op.drop_table("chat_messages")
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_analysis_runs_conversation_id"))

    op.drop_table("analysis_runs")
    op.drop_table("conversations")
