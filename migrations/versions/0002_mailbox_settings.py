"""Store the initial 263 mailbox configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0002_mailbox_settings"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mailbox_settings",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("address", sa.String(254), nullable=False),
        sa.Column("host", sa.String(32), nullable=False),
        sa.Column("folder", sa.String(128), nullable=False),
        sa.Column("since", sa.Date(), nullable=False),
        sa.Column("poll_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("encrypted_password", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint("id = 1", name="mailbox_settings_singleton"),
        sa.CheckConstraint("poll_minutes BETWEEN 1 AND 60", name="mailbox_poll_range"),
    )


def downgrade() -> None:
    op.drop_table("mailbox_settings")
