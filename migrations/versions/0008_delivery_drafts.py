"""Store customer material mappings and reproducible delivery drafts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_delivery_drafts"
down_revision = "0007_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_mappings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("customer", sa.String(16), nullable=False),
        sa.Column("customer_item", sa.String(80), nullable=False),
        sa.Column("order_no", sa.String(80)),
        sa.Column("order_key", sa.String(80)),
        sa.Column("order_line", sa.String(80)),
        sa.Column("ship_to", sa.String(80)),
        sa.Column("supplier_item", sa.String(80)),
        sa.Column("internal_item", sa.String(80), nullable=False),
        sa.Column("drawing", sa.String(80)),
        sa.Column("minimum_pack", sa.Integer()),
        sa.Column("units_per_carton", sa.Integer()),
        sa.Column("cartons_per_pallet", sa.Integer()),
        sa.Column("safety_stock", sa.Integer()),
        sa.UniqueConstraint(
            "customer",
            "customer_item",
            "order_key",
            "order_line",
            "ship_to",
            name="uq_material_mapping_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "delivery_drafts",
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("parse_runs.id"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("relay_users.id"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("delivery_drafts")
    op.drop_table("material_mappings")
