"""Add approval timestamps and bonus review queue markers.

Revision ID: 20260906_0011
Revises: 20260905_0010
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0011"
down_revision: str | None = "20260905_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_cards_approved_at", "cards", ["approved_at"], unique=False)
    op.add_column(
        "review_session_cards",
        sa.Column("is_bonus", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("review_session_cards", "is_bonus")
    op.drop_index("ix_cards_approved_at", table_name="cards")
    op.drop_column("cards", "approved_at")
