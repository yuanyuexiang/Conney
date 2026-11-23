"""Track mailbox cursors and terminal message intake errors."""

import sqlalchemy as sa
from alembic import op

revision = "0004_sync_cursor"
down_revision = "0003_intake_and_parse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("received_mail", sa.Column("intake_error_code", sa.String(64), nullable=True))
    op.create_table(
        "mail_sync_cursors",
        sa.Column("source", sa.String(512), primary_key=True),
        sa.Column("folder", sa.String(255), primary_key=True),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("last_uid", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("mail_sync_cursors")
    op.drop_column("received_mail", "intake_error_code")
