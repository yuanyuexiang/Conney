"""Allow SMTP to reference the credential saved by IMAP."""

from alembic import op

revision = "0016_shared_mailbox"
down_revision = "0015_release_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE outgoing_mailbox ALTER COLUMN encrypted_password DROP NOT NULL")


def downgrade() -> None:
    # A shared credential must be converted to a standalone one before downgrading.
    op.execute("ALTER TABLE outgoing_mailbox ALTER COLUMN encrypted_password SET NOT NULL")
