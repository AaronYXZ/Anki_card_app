"""Add FSRS state and daily review queue tracking.

Revision ID: 20260810_0004
Revises: 20260810_0003
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0004"
down_revision: str | None = "20260810_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def timestamp_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.add_column("scheduling_states", sa.Column("fsrs_card", sa.JSON(), nullable=True))
    op.add_column(
        "scheduling_states",
        sa.Column("review_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "review_sessions",
        sa.Column("reviewed_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("review_logs", sa.Column("response_time_ms", sa.Integer(), nullable=True))
    op.add_column(
        "review_logs",
        sa.Column("was_new", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        "ck_scheduling_states_review_count_nonnegative",
        "scheduling_states",
        "review_count >= 0",
    )
    op.create_check_constraint(
        "ck_review_sessions_queue_size_nonnegative",
        "review_sessions",
        "queue_size >= 0",
    )
    op.create_check_constraint(
        "ck_review_sessions_reviewed_count_range",
        "review_sessions",
        "reviewed_count >= 0 AND reviewed_count <= queue_size",
    )
    op.create_check_constraint(
        "ck_review_logs_response_time_nonnegative",
        "review_logs",
        "response_time_ms IS NULL OR response_time_ms >= 0",
    )

    op.create_table(
        "review_session_cards",
        uuid_column("id"),
        uuid_column("review_session_id"),
        uuid_column("card_id"),
        sa.Column("position", sa.Integer(), nullable=False),
        timestamp_column("revealed_at", nullable=True),
        timestamp_column("completed_at", nullable=True),
        sa.CheckConstraint("position >= 0", name="ck_review_session_cards_position_nonnegative"),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_session_id"], ["review_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_session_id", "card_id", name="uq_review_session_cards_card"),
        sa.UniqueConstraint(
            "review_session_id", "position", name="uq_review_session_cards_position"
        ),
    )
    op.create_index("ix_review_session_cards_card_id", "review_session_cards", ["card_id"])
    op.create_index(
        "ix_review_session_cards_review_session_id",
        "review_session_cards",
        ["review_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_session_cards_review_session_id", table_name="review_session_cards")
    op.drop_index("ix_review_session_cards_card_id", table_name="review_session_cards")
    op.drop_table("review_session_cards")
    op.drop_constraint("ck_review_logs_response_time_nonnegative", "review_logs", type_="check")
    op.drop_constraint("ck_review_sessions_reviewed_count_range", "review_sessions", type_="check")
    op.drop_constraint(
        "ck_review_sessions_queue_size_nonnegative", "review_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_scheduling_states_review_count_nonnegative", "scheduling_states", type_="check"
    )
    op.drop_column("review_logs", "was_new")
    op.drop_column("review_logs", "response_time_ms")
    op.drop_column("review_sessions", "reviewed_count")
    op.drop_column("scheduling_states", "review_count")
    op.drop_column("scheduling_states", "fsrs_card")
