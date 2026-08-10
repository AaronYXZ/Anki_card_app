"""Add Skeleton Recall card type.

Revision ID: 20260810_0005
Revises: 20260810_0004
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260810_0005"
down_revision: str | None = "20260810_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_cards_card_type", "cards", type_="check")
    op.create_check_constraint(
        "ck_cards_card_type",
        "cards",
        "card_type IN ('normal', 'cloze', 'skeleton_recall')",
    )


def downgrade() -> None:
    op.execute("UPDATE cards SET card_type = 'normal' WHERE card_type = 'skeleton_recall'")
    op.drop_constraint("ck_cards_card_type", "cards", type_="check")
    op.create_check_constraint(
        "ck_cards_card_type",
        "cards",
        "card_type IN ('normal', 'cloze')",
    )
