"""Retain deleted account identities for business records."""

import sqlalchemy as sa
from alembic import op

revision = "0009_user_deletion"
down_revision = "0008_delivery_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("relay_users", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "deleted_user_is_inactive", "relay_users", "deleted_at IS NULL OR NOT active"
    )
    op.drop_constraint("relay_users_username_key", "relay_users", type_="unique")
    op.create_index(
        "uq_relay_users_live_username",
        "relay_users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    # Keep retired IDs and reserve an unloginable name under the old global uniqueness rule.
    op.execute("UPDATE relay_users SET username = '~deleted:' || id WHERE deleted_at IS NOT NULL")
    op.drop_index("uq_relay_users_live_username", table_name="relay_users")
    op.create_unique_constraint("relay_users_username_key", "relay_users", ["username"])
    op.drop_constraint("deleted_user_is_inactive", "relay_users", type_="check")
    op.drop_column("relay_users", "deleted_at")
