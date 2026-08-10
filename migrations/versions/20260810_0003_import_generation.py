"""Add source import and AI generation tracking.

Revision ID: 20260810_0003
Revises: 20260810_0002
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0003"
down_revision: str | None = "20260810_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def timestamp_column(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "generation_runs",
        uuid_column("id"),
        uuid_column("user_id"),
        uuid_column("source_document_id"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("completed_chunks", sa.Integer(), nullable=False),
        sa.Column("generated_cards", sa.Integer(), nullable=False),
        sa.Column("failed_chunks", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        timestamp_column("created_at"),
        timestamp_column("started_at", nullable=True),
        timestamp_column("completed_at", nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'failed')",
            name="ck_generation_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_runs_source_document_id", "generation_runs", ["source_document_id"]
    )
    op.create_index("ix_generation_runs_status", "generation_runs", ["status"])
    op.create_index("ix_generation_runs_user_id", "generation_runs", ["user_id"])

    op.add_column("cards", uuid_column("source_chunk_id", nullable=True))
    op.add_column("cards", uuid_column("generation_run_id", nullable=True))
    op.add_column("cards", sa.Column("content_fingerprint", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_cards_source_chunk_id",
        "cards",
        "source_chunks",
        ["source_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_cards_generation_run_id",
        "cards",
        "generation_runs",
        ["generation_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_cards_source_chunk_id", "cards", ["source_chunk_id"])
    op.create_index("ix_cards_generation_run_id", "cards", ["generation_run_id"])
    op.create_unique_constraint(
        "uq_cards_user_fingerprint", "cards", ["user_id", "content_fingerprint"]
    )

    op.add_column("card_versions", sa.Column("source_excerpt", sa.Text(), nullable=True))
    op.add_column("card_versions", sa.Column("ai_enrichment", sa.Text(), nullable=True))

    op.create_table(
        "generation_chunk_runs",
        uuid_column("id"),
        uuid_column("generation_run_id"),
        uuid_column("source_chunk_id"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("generated_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        timestamp_column("started_at", nullable=True),
        timestamp_column("completed_at", nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_generation_chunk_attempts_nonnegative"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_generation_chunk_runs_status",
        ),
        sa.ForeignKeyConstraint(["generation_run_id"], ["generation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_chunk_id"], ["source_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_run_id", "source_chunk_id", name="uq_generation_chunk_runs_chunk"
        ),
    )
    op.create_index(
        "ix_generation_chunk_runs_generation_run_id", "generation_chunk_runs", ["generation_run_id"]
    )
    op.create_index(
        "ix_generation_chunk_runs_source_chunk_id", "generation_chunk_runs", ["source_chunk_id"]
    )
    op.create_index("ix_generation_chunk_runs_status", "generation_chunk_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_generation_chunk_runs_status", table_name="generation_chunk_runs")
    op.drop_index("ix_generation_chunk_runs_source_chunk_id", table_name="generation_chunk_runs")
    op.drop_index("ix_generation_chunk_runs_generation_run_id", table_name="generation_chunk_runs")
    op.drop_table("generation_chunk_runs")
    op.drop_column("card_versions", "ai_enrichment")
    op.drop_column("card_versions", "source_excerpt")
    op.drop_constraint("uq_cards_user_fingerprint", "cards", type_="unique")
    op.drop_index("ix_cards_generation_run_id", table_name="cards")
    op.drop_index("ix_cards_source_chunk_id", table_name="cards")
    op.drop_constraint("fk_cards_generation_run_id", "cards", type_="foreignkey")
    op.drop_constraint("fk_cards_source_chunk_id", "cards", type_="foreignkey")
    op.drop_column("cards", "content_fingerprint")
    op.drop_column("cards", "generation_run_id")
    op.drop_column("cards", "source_chunk_id")
    op.drop_index("ix_generation_runs_user_id", table_name="generation_runs")
    op.drop_index("ix_generation_runs_status", table_name="generation_runs")
    op.drop_index("ix_generation_runs_source_document_id", table_name="generation_runs")
    op.drop_table("generation_runs")
