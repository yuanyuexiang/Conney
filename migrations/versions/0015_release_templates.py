"""Allow all contract templates to use the delivery release lifecycle."""

from alembic import op

revision = "0015_release_templates"
down_revision = "0014_delivery_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE delivery_releases
            ALTER COLUMN ship_through DROP NOT NULL,
            ADD COLUMN template text NOT NULL DEFAULT 'drx_delfor',
            ADD COLUMN retained_issues integer NOT NULL DEFAULT 0,
            ADD CONSTRAINT delivery_release_template_scope CHECK (
                (template = 'drx_delfor' AND ship_through IS NOT NULL AND retained_issues = 0)
                OR (
                    template IN ('kroschu_4905', 'jlr_delfor', 'jlr_deljit', 'vitesco_delfor')
                    AND ship_through IS NULL AND retained_issues >= 0
                )
            );
    """)


def downgrade() -> None:
    # PostgreSQL rejects restoring NOT NULL when a forecast release has been created.
    op.execute("""
        ALTER TABLE delivery_releases
            DROP CONSTRAINT delivery_release_template_scope,
            ALTER COLUMN ship_through SET NOT NULL,
            DROP COLUMN retained_issues,
            DROP COLUMN template;
    """)
