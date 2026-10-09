"""job priority, target worker, version matching and leases (spec 0020 part C)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import raceforge.backend.db

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Expand only: every column is nullable or has a server default, so the old API keeps working.
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("priority", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("target_worker_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("raceforge_version", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("attempt", sa.Integer(), server_default="1", nullable=False))
        batch_op.add_column(
            sa.Column("queued_at", raceforge.backend.db.UtcDateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("queued_at")
        batch_op.drop_column("attempt")
        batch_op.drop_column("raceforge_version")
        batch_op.drop_column("target_worker_id")
        batch_op.drop_column("priority")
