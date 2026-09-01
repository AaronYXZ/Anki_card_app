"""Add structured study notes and sibling card templates.

Revision ID: 20260831_0009
Revises: 20260827_0008
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0009"
down_revision: str | None = "20260827_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "study_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("note_type", sa.String(length=32), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_study_notes_user_id", "study_notes", ["user_id"], unique=False)
    op.create_index("ix_study_notes_note_type", "study_notes", ["note_type"], unique=False)
    op.add_column("cards", sa.Column("note_id", sa.Uuid(), nullable=True))
    op.add_column("cards", sa.Column("template_key", sa.String(length=32), nullable=True))
    op.create_index("ix_cards_note_id", "cards", ["note_id"], unique=False)
    op.create_foreign_key(
        "fk_cards_note_id", "cards", "study_notes", ["note_id"], ["id"], ondelete="CASCADE"
    )
    op.create_unique_constraint("uq_cards_note_template", "cards", ["note_id", "template_key"])
    op.create_check_constraint(
        "ck_cards_note_template_pair",
        "cards",
        "(note_id IS NULL AND template_key IS NULL) OR "
        "(note_id IS NOT NULL AND template_key IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_cards_note_template_pair", "cards", type_="check")
    op.drop_constraint("uq_cards_note_template", "cards", type_="unique")
    op.drop_constraint("fk_cards_note_id", "cards", type_="foreignkey")
    op.drop_index("ix_cards_note_id", table_name="cards")
    op.drop_column("cards", "template_key")
    op.drop_column("cards", "note_id")
    op.drop_index("ix_study_notes_note_type", table_name="study_notes")
    op.drop_index("ix_study_notes_user_id", table_name="study_notes")
    op.drop_table("study_notes")
