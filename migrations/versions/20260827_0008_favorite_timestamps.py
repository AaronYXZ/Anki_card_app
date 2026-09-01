"""Add favorite timestamps.

Revision ID: 20260827_0008
Revises: 20260827_0007
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0008"
down_revision: str | None = "20260827_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("favorited_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE cards SET favorited_at = updated_at WHERE is_favorite = true"))
    op.create_index("ix_cards_favorited_at", "cards", ["favorited_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cards_favorited_at", table_name="cards")
    op.drop_column("cards", "favorited_at")
