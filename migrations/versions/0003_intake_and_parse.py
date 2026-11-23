"""Persist mail intake, attachment archives, and original parse results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_intake_and_parse"
down_revision = "0002_mailbox_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "received_mail",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source", sa.String(512), nullable=False),
        sa.Column("folder", sa.String(255), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "folder", "uidvalidity", "uid", name="mail_receive_identity"),
    )
    op.create_table(
        "archive_objects",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "received_attachments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("mail_id", sa.BigInteger(), sa.ForeignKey("received_mail.id"), nullable=False),
        sa.Column("mime_part", sa.String(100), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), sa.ForeignKey("archive_objects.sha256"), nullable=True),
        sa.Column("intake_state", sa.String(32), nullable=False),
        sa.UniqueConstraint("mail_id", "mime_part", name="received_attachment_identity"),
        sa.CheckConstraint(
            "intake_state IN ('archived', 'unsupported', 'rejected')",
            name="accepted_attachment_intake_state",
        ),
    )
    op.create_table(
        "parse_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "attachment_id",
            sa.BigInteger(),
            sa.ForeignKey("received_attachments.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'succeeded', 'failed')",
            name="valid_parse_job_state",
        ),
        sa.CheckConstraint(
            "(state = 'processing' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) "
            "OR (state != 'processing' AND lease_token IS NULL AND lease_until IS NULL)",
            name="valid_parse_job_lease",
        ),
    )
    op.create_index(
        "ix_parse_jobs_pending",
        "parse_jobs",
        ["available_at", "id"],
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_index(
        "ix_parse_jobs_expired",
        "parse_jobs",
        ["lease_until", "id"],
        postgresql_where=sa.text("state = 'processing'"),
    )
    op.create_table(
        "parse_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "job_id", sa.BigInteger(), sa.ForeignKey("parse_jobs.id"), nullable=False, unique=True
        ),
        sa.Column("template", sa.String(50), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("source_fields", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "parsed_material_blocks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("parse_runs.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_fields", JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "position", name="parsed_material_block_position"),
    )
    op.create_table(
        "parsed_quantity_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "block_id", sa.BigInteger(), sa.ForeignKey("parsed_material_blocks.id"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("requested_date", sa.Date(), nullable=True),
        sa.Column("earliest_date", sa.Date(), nullable=True),
        sa.Column("latest_date", sa.Date(), nullable=True),
        sa.Column("source_fields", JSONB(), nullable=False),
        sa.UniqueConstraint("block_id", "position", name="parsed_quantity_record_position"),
    )


def downgrade() -> None:
    op.drop_table("parsed_quantity_records")
    op.drop_table("parsed_material_blocks")
    op.drop_table("parse_runs")
    op.drop_table("parse_jobs")
    op.drop_table("received_attachments")
    op.drop_table("archive_objects")
    op.drop_table("received_mail")
