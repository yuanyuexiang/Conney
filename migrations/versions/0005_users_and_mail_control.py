"""Create managed users, sessions, and mailbox synchronization controls."""

import sqlalchemy as sa
from alembic import op

revision = "0005_users_and_mail_control"
down_revision = "0004_sync_cursor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.LargeBinary(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name="valid_user_role"),
    )
    op.create_table(
        "relay_sessions",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("relay_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_relay_sessions_user", "relay_sessions", ["user_id"])
    op.create_index("ix_relay_sessions_expiry", "relay_sessions", ["expires_at"])
    op.add_column("mailbox_settings", sa.Column("revision", sa.Uuid(), nullable=True))
    op.execute("UPDATE mailbox_settings SET revision = gen_random_uuid()")
    op.alter_column("mailbox_settings", "revision", nullable=False)
    op.add_column("mailbox_settings", sa.Column("tested_revision", sa.Uuid(), nullable=True))
    op.add_column(
        "mailbox_settings",
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "mailbox_settings", sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("mailbox_settings", sa.Column("last_sync_result", sa.String(40), nullable=True))
    op.add_column("mailbox_settings", sa.Column("last_sync_count", sa.Integer(), nullable=True))

    op.create_check_constraint(
        "enabled_mailbox_has_verified_revision",
        "mailbox_settings",
        "NOT enabled OR (tested_revision IS NOT NULL AND tested_revision = revision)",
    )


def downgrade() -> None:
    op.drop_constraint("enabled_mailbox_has_verified_revision", "mailbox_settings", type_="check")
    for column in (
        "last_sync_count",
        "last_sync_result",
        "last_sync_at",
        "enabled",
        "tested_revision",
        "revision",
    ):
        op.drop_column("mailbox_settings", column)
    op.drop_table("relay_sessions")
    op.drop_table("relay_users")
