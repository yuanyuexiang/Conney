"""Persist parser coordinates and index raw dates that require review."""

import sqlalchemy as sa
from alembic import op

revision = "0006_workbench_diagnostics"
down_revision = "0005_users_and_mail_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parse_jobs", sa.Column("error_location", sa.Text(), nullable=True))
    op.add_column("parse_jobs", sa.Column("error_reason", sa.Text(), nullable=True))
    op.create_index(
        "ix_quantity_special_date",
        "parsed_quantity_records",
        ["block_id", "id"],
        postgresql_where=sa.text("source_fields->'DateTime'->>'value' = '000000'"),
    )


def downgrade() -> None:
    op.drop_index("ix_quantity_special_date", table_name="parsed_quantity_records")
    op.drop_column("parse_jobs", "error_reason")
    op.drop_column("parse_jobs", "error_location")
