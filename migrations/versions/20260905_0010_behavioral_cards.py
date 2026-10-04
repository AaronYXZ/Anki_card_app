"""Add behavioral generation profiles and story card metadata.

Revision ID: 20260905_0010
Revises: 20260831_0009
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0010"
down_revision: str | None = "20260831_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column(
            "generation_profile",
            sa.String(length=16),
            server_default="general",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_generation_runs_generation_profile",
        "generation_runs",
        ["generation_profile"],
        unique=False,
    )
    op.add_column(
        "cards",
        sa.Column("tags", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.add_column("cards", sa.Column("story_id", sa.String(length=128), nullable=True))
    op.add_column("cards", sa.Column("story_name", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("card_role", sa.String(length=64), nullable=True))
    op.add_column("cards", sa.Column("main_story_card_id", sa.Uuid(), nullable=True))
    op.create_index("ix_cards_story_id", "cards", ["story_id"], unique=False)
    op.create_index(
        "ix_cards_main_story_card_id", "cards", ["main_story_card_id"], unique=False
    )
    op.create_foreign_key(
        "fk_cards_main_story_card_id",
        "cards",
        "cards",
        ["main_story_card_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("ck_cards_card_type", "cards", type_="check")
    op.create_check_constraint(
        "ck_cards_card_type",
        "cards",
        "card_type IN ('normal', 'cloze', 'skeleton_recall', "
        "'behavioral_main', 'behavioral_carl', 'behavioral_q')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE cards SET card_type = 'normal' "
        "WHERE card_type IN ('behavioral_main', 'behavioral_carl', 'behavioral_q')"
    )
    op.drop_constraint("ck_cards_card_type", "cards", type_="check")
    op.create_check_constraint(
        "ck_cards_card_type",
        "cards",
        "card_type IN ('normal', 'cloze', 'skeleton_recall')",
    )
    op.drop_constraint("fk_cards_main_story_card_id", "cards", type_="foreignkey")
    op.drop_index("ix_cards_main_story_card_id", table_name="cards")
    op.drop_index("ix_cards_story_id", table_name="cards")
    op.drop_column("cards", "main_story_card_id")
    op.drop_column("cards", "card_role")
    op.drop_column("cards", "story_name")
    op.drop_column("cards", "story_id")
    op.drop_column("cards", "tags")
    op.drop_index("ix_generation_runs_generation_profile", table_name="generation_runs")
    op.drop_column("generation_runs", "generation_profile")
