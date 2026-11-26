"""Retain the revision of a deleted delivery draft for safe regeneration."""

import sqlalchemy as sa
from alembic import op

revision = "0011_delivery_draft_deletion"
down_revision = "0010_material_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("delivery_drafts", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.execute("DELETE FROM delivery_drafts WHERE deleted_at IS NOT NULL")
    op.drop_column("delivery_drafts", "deleted_at")
