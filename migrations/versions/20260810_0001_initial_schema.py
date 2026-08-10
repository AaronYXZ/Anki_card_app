"""Establish the initial database migration baseline.

Revision ID: 20260810_0001
Revises:
Create Date: 2026-08-10
"""

from collections.abc import Sequence

revision: str = "20260810_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish an empty baseline before domain tables are introduced."""


def downgrade() -> None:
    """Remove the empty baseline."""
