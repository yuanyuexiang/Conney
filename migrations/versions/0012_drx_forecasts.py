"""Apply the DRX interval forecast and split the draft follow-up counts."""

from alembic import op

revision = "0012_drx_forecasts"
down_revision = "0011_delivery_draft_deletion"
branch_labels = None
depends_on = None


def _update_snapshots(*, upgrade: bool) -> None:
    old_state, new_state = (
        ("period_review", "confirmed_interval")
        if upgrade
        else ("confirmed_interval", "period_review")
    )
    old_version, new_version = ("drx-1", "drx-2") if upgrade else ("drx-2", "drx-1")
    pending = "counts.review" if upgrade else "counts.review + counts.shipments"
    shipments = "|| jsonb_build_object('pending_shipments', counts.shipments)" if upgrade else ""
    op.execute(f"""
        WITH updated AS (
            SELECT d.run_id, COALESCE((
                SELECT jsonb_agg(
                    jsonb_set(m.value, '{{entries}}', transformed.entries) ORDER BY m.position
                )
                FROM jsonb_array_elements(d.snapshot->'materials')
                    WITH ORDINALITY AS m(value, position)
                CROSS JOIN LATERAL (
                    SELECT COALESCE(jsonb_agg(
                        CASE WHEN e.value->>'state' = '{old_state}'
                               AND e.value->>'review' IS NULL
                               AND e.value->>'requested_date' IS NULL
                               AND e.value->>'earliest_date' <= e.value->>'latest_date'
                            THEN jsonb_set(e.value, '{{state}}', to_jsonb('{new_state}'::text))
                            ELSE e.value END
                        ORDER BY e.position
                    ), '[]'::jsonb) AS entries
                    FROM jsonb_array_elements(m.value->'entries')
                        WITH ORDINALITY AS e(value, position)
                ) AS transformed
            ), '[]'::jsonb) AS materials
            FROM delivery_drafts AS d
            WHERE d.deleted_at IS NULL AND d.snapshot->>'rule_version' = '{old_version}'
        )
        UPDATE delivery_drafts AS d
        SET snapshot = (d.snapshot - 'pending_shipments') || jsonb_build_object(
            'rule_version', '{new_version}', 'materials', updated.materials,
            'pending_entries', {pending}
        ) {shipments}, revision = d.revision + 1, updated_at = now()
        FROM updated
        CROSS JOIN LATERAL (
            SELECT count(*) FILTER (WHERE e.value->>'state' IN ('code_review', 'period_review'))
                       AS review,
                   count(*) FILTER (WHERE e.value->>'state' = 'transport_missing') AS shipments
            FROM jsonb_array_elements(updated.materials) AS m(value)
            CROSS JOIN LATERAL jsonb_array_elements(m.value->'entries') AS e(value)
        ) AS counts
        WHERE d.run_id = updated.run_id
    """)


def upgrade() -> None:
    _update_snapshots(upgrade=True)


def downgrade() -> None:
    _update_snapshots(upgrade=False)
