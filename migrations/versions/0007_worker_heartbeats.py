"""Record the latest progress reported by each background process."""

import sqlalchemy as sa
from alembic import op

revision = "0007_worker_heartbeats"
down_revision = "0006_workbench_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker", sa.String(12), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("worker IN ('mail', 'parser')", name="known_worker"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
