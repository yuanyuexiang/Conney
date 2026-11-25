"""Store material identifiers observed in customer workbooks."""

import sqlalchemy as sa
from alembic import op

revision = "0010_material_candidates"
down_revision = "0009_user_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("customer", sa.String(16), nullable=False),
        sa.Column("customer_item", sa.String(80), nullable=False),
        sa.Column("order_no", sa.String(80)),
        sa.Column("order_key", sa.String(80)),
        sa.Column("order_line", sa.String(80)),
        sa.Column("ship_to", sa.String(80)),
        sa.Column("supplier_item", sa.String(80)),
        sa.Column("source_filename", sa.Text(), nullable=False),
        sa.Column("source_cell", sa.Text(), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.UniqueConstraint(
            "customer",
            "customer_item",
            "order_key",
            "order_line",
            "ship_to",
            "supplier_item",
            name="uq_material_candidate_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("material_candidates")
