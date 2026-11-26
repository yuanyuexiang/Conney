import re
from datetime import date, datetime
from hashlib import sha256

from pydantic import BaseModel
from sqlalchemy import Connection, text

from relay.services.delivery import DraftSnapshot
from relay.services.delivery_xlsx import build_workbook

_WORKBOOK_LIMIT = 10 * 1024 * 1024

RELEASE_COLUMNS = """
    r.id, r.run_id, r.version, r.draft_revision, r.rule_version, r.output_version,
    r.ship_through, r.document_no, r.filename, r.confirmed_at,
    u.username AS confirmed_by
"""


class ReleaseBlocker(BaseModel):
    kind: str
    customer_item: str | None
    order_no: str | None
    cell: str | None
    requested_date: date | None = None


class ReleasePreview(BaseModel):
    pending_mappings: int
    pending_reviews: int
    pending_shipments: int
    future_unscheduled: int
    scheduled: int
    blockers: list[ReleaseBlocker]

    @property
    def blocked(self) -> bool:
        return bool(self.pending_mappings or self.pending_reviews or self.pending_shipments)


class DeliveryRelease(BaseModel):
    id: int
    run_id: int
    version: int
    draft_revision: int
    rule_version: str
    output_version: str
    ship_through: date
    document_no: str | None
    filename: str
    confirmed_at: datetime
    confirmed_by: str


def check_release(draft: DraftSnapshot, ship_through: date) -> ReleasePreview:
    result = ReleasePreview(
        pending_mappings=0,
        pending_reviews=0,
        pending_shipments=0,
        future_unscheduled=0,
        scheduled=0,
        blockers=[],
    )
    for material in draft.materials:
        if material.mapping_state != "matched":
            result.pending_mappings += 1
            if len(result.blockers) < 20:
                result.blockers.append(
                    ReleaseBlocker(
                        kind=material.mapping_state,
                        customer_item=material.customer_item,
                        order_no=material.order_no,
                        cell=material.customer_item_cell,
                    )
                )
        for entry in material.entries:
            kind: str
            required_date = (
                entry.review.delivery_date if entry.review else None
            ) or entry.requested_date
            if entry.state in {"code_review", "period_review"}:
                result.pending_reviews += 1
                kind = entry.state
            elif entry.state == "transport_missing":
                if required_date is not None and required_date > ship_through:
                    result.future_unscheduled += 1
                    continue
                result.pending_shipments += 1
                kind = "transport_missing"
            else:
                result.scheduled += int(entry.shipment is not None)
                continue
            if len(result.blockers) < 20:
                result.blockers.append(
                    ReleaseBlocker(
                        kind=kind,
                        customer_item=material.customer_item,
                        order_no=material.order_no,
                        cell=entry.quantity_cell,
                        requested_date=required_date,
                    )
                )
    return result


def list_releases(connection: Connection, run_id: int) -> list[DeliveryRelease]:
    rows = connection.execute(
        text(f"""
            SELECT {RELEASE_COLUMNS} FROM delivery_releases AS r
            JOIN relay_users AS u ON u.id = r.confirmed_by
            WHERE r.run_id = :run_id ORDER BY r.version DESC
        """),
        {"run_id": run_id},
    ).mappings()
    return [DeliveryRelease.model_validate(row) for row in rows]


def freeze_release(
    connection: Connection,
    draft: DraftSnapshot,
    *,
    run_id: int,
    revision: int,
    version: int,
    ship_through: date,
    actor_id: int,
) -> DeliveryRelease:
    document_no = re.sub(r"[^A-Za-z0-9_-]+", "-", (draft.document_no or str(run_id)))[:48]
    filename = f"德科交付计划_报文{document_no}_V{version}.xlsx"
    workbook = build_workbook(draft, release_version=version, ship_through=ship_through)
    if len(workbook) > _WORKBOOK_LIMIT:
        raise ValueError("release_workbook_too_large")
    connection.execute(
        text("""
            INSERT INTO delivery_releases (
                run_id, version, draft_revision, ship_through, rule_version, output_version,
                document_no, filename, workbook, checksum, confirmed_by
            ) VALUES (
                :run_id, :version, :revision, :ship_through, :rule_version, :output_version,
                :document_no, :filename, :workbook, :checksum, :actor_id
            )
        """),
        {
            "run_id": run_id,
            "version": version,
            "revision": revision,
            "ship_through": ship_through,
            "rule_version": draft.rule_version,
            "output_version": draft.output_version,
            "document_no": draft.document_no,
            "filename": filename,
            "workbook": workbook,
            "checksum": sha256(workbook).hexdigest(),
            "actor_id": actor_id,
        },
    )
    return list_releases(connection, run_id)[0]
