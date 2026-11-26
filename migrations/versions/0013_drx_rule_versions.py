"""Store the tested and published DRX transport rule versions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_drx_rule_versions"
down_revision = "0012_drx_forecasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drx_rule_versions",
        sa.Column("id", sa.Integer(), sa.Identity(start=3), primary_key=True),
        sa.Column("air_days", sa.Integer(), nullable=False),
        sa.Column("sea_days", sa.Integer(), nullable=False),
        sa.Column("source_note", sa.String(240), nullable=False),
        sa.Column("baseline_id", sa.Integer(), sa.ForeignKey("drx_rule_versions.id")),
        sa.Column("trial", JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("relay_users.id")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_by", sa.BigInteger(), sa.ForeignKey("relay_users.id")),
        sa.CheckConstraint("air_days BETWEEN 0 AND 365 AND sea_days BETWEEN 0 AND 365"),
    )
    op.execute("""
        INSERT INTO drx_rule_versions (id, air_days, sea_days, source_note, published_at)
        VALUES (2, 14, 84, '交付规则与开发验收 · 德科 DRX 空海运时效', now())
    """)
    op.create_table(
        "delivery_draft_rule_history",
        sa.Column(
            "run_id", sa.BigInteger(), sa.ForeignKey("delivery_drafts.run_id"), primary_key=True
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column(
            "replaced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("replaced_by", sa.BigInteger(), sa.ForeignKey("relay_users.id"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("delivery_draft_rule_history")
    op.drop_table("drx_rule_versions")
