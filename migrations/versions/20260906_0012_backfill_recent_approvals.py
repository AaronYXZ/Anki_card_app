"""Backfill recent approval timestamps.

Revision ID: 20260906_0012
Revises: 20260906_0011
Create Date: 2026-09-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260906_0012"
down_revision: str | None = "20260906_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE cards "
        "SET approved_at = updated_at "
        "WHERE approved_at IS NULL "
        "AND state = 'active' "
        "AND updated_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'"
    )


def downgrade() -> None:
    # Historical approval events were not stored, so the backfilled values cannot be
    # distinguished safely from genuine approval timestamps after this migration runs.
    pass
