"""Add the initial learning domain model.

Revision ID: 20260810_0002
Revises: 20260810_0001
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0002"
down_revision: str | None = "20260810_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def timestamp_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "user_accounts",
        uuid_column("id"),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("desired_retention", sa.Float(), nullable=False),
        timestamp_column("created_at"),
        timestamp_column("updated_at"),
        sa.CheckConstraint("daily_limit > 0", name="ck_user_accounts_daily_limit_positive"),
        sa.CheckConstraint(
            "desired_retention > 0 AND desired_retention <= 1",
            name="ck_user_accounts_desired_retention_range",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_accounts_email", "user_accounts", ["email"], unique=True)

    op.create_table(
        "source_documents",
        uuid_column("id"),
        uuid_column("user_id"),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        timestamp_column("source_modified_at", nullable=True),
        timestamp_column("imported_at"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_hash", name="uq_source_documents_user_hash"),
    )
    op.create_index("ix_source_documents_user_id", "source_documents", ["user_id"])

    op.create_table(
        "source_chunks",
        uuid_column("id"),
        uuid_column("source_document_id"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=True),
        timestamp_column("created_at"),
        sa.CheckConstraint("sequence >= 0", name="ck_source_chunks_sequence_nonnegative"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_document_id", "sequence", name="uq_source_chunks_sequence"),
    )
    op.create_index("ix_source_chunks_source_document_id", "source_chunks", ["source_document_id"])

    op.create_table(
        "cards",
        uuid_column("id"),
        uuid_column("user_id"),
        uuid_column("source_document_id", nullable=True),
        sa.Column("card_type", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        uuid_column("current_version_id", nullable=True),
        timestamp_column("created_at"),
        timestamp_column("updated_at"),
        sa.CheckConstraint("card_type IN ('normal', 'cloze')", name="ck_cards_card_type"),
        sa.CheckConstraint(
            "state IN ('draft', 'active', 'suspended', 'retired', 'rejected')",
            name="ck_cards_state",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cards_source_document_id", "cards", ["source_document_id"])
    op.create_index("ix_cards_state", "cards", ["state"])
    op.create_index("ix_cards_user_id", "cards", ["user_id"])

    op.create_table(
        "card_versions",
        uuid_column("id"),
        uuid_column("card_id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("front", sa.Text(), nullable=True),
        sa.Column("back", sa.Text(), nullable=True),
        sa.Column("cloze_text", sa.Text(), nullable=True),
        sa.Column("back_extra", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        timestamp_column("created_at"),
        sa.CheckConstraint("version_number > 0", name="ck_card_versions_number_positive"),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("card_id", "version_number", name="uq_card_versions_number"),
    )
    op.create_index("ix_card_versions_card_id", "card_versions", ["card_id"])
    op.create_foreign_key(
        "fk_cards_current_version",
        "cards",
        "card_versions",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "scheduling_states",
        uuid_column("card_id"),
        timestamp_column("due_at"),
        sa.Column("stability", sa.Float(), nullable=True),
        sa.Column("difficulty", sa.Float(), nullable=True),
        sa.Column("scheduler_state", sa.String(length=32), nullable=False),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=True),
        timestamp_column("last_review_at", nullable=True),
        timestamp_column("updated_at"),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("card_id"),
    )
    op.create_index("ix_scheduling_states_due_at", "scheduling_states", ["due_at"])

    op.create_table(
        "review_sessions",
        uuid_column("id"),
        uuid_column("user_id"),
        sa.Column("queue_size", sa.Integer(), nullable=False),
        timestamp_column("started_at"),
        timestamp_column("completed_at", nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_sessions_user_id", "review_sessions", ["user_id"])

    op.create_table(
        "review_logs",
        uuid_column("id"),
        uuid_column("attempt_id"),
        uuid_column("user_id"),
        uuid_column("card_id"),
        uuid_column("review_session_id", nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        timestamp_column("reviewed_at"),
        sa.Column("elapsed_days", sa.Float(), nullable=True),
        sa.Column("prior_state", sa.JSON(), nullable=False),
        sa.Column("new_state", sa.JSON(), nullable=False),
        sa.CheckConstraint("rating >= 1 AND rating <= 4", name="ck_review_logs_rating_range"),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_session_id"], ["review_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )
    op.create_index("ix_review_logs_card_id", "review_logs", ["card_id"])
    op.create_index("ix_review_logs_review_session_id", "review_logs", ["review_session_id"])
    op.create_index("ix_review_logs_user_id", "review_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_review_logs_user_id", table_name="review_logs")
    op.drop_index("ix_review_logs_review_session_id", table_name="review_logs")
    op.drop_index("ix_review_logs_card_id", table_name="review_logs")
    op.drop_table("review_logs")
    op.drop_index("ix_review_sessions_user_id", table_name="review_sessions")
    op.drop_table("review_sessions")
    op.drop_index("ix_scheduling_states_due_at", table_name="scheduling_states")
    op.drop_table("scheduling_states")
    op.drop_constraint("fk_cards_current_version", "cards", type_="foreignkey")
    op.drop_index("ix_card_versions_card_id", table_name="card_versions")
    op.drop_table("card_versions")
    op.drop_index("ix_cards_user_id", table_name="cards")
    op.drop_index("ix_cards_state", table_name="cards")
    op.drop_index("ix_cards_source_document_id", table_name="cards")
    op.drop_table("cards")
    op.drop_index("ix_source_chunks_source_document_id", table_name="source_chunks")
    op.drop_table("source_chunks")
    op.drop_index("ix_source_documents_user_id", table_name="source_documents")
    op.drop_table("source_documents")
    op.drop_index("ix_user_accounts_email", table_name="user_accounts")
    op.drop_table("user_accounts")
