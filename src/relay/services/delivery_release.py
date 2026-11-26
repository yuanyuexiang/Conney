import re
from datetime import date, datetime
from hashlib import sha256

from pydantic import BaseModel
from sqlalchemy import Connection, text

from relay.services.delivery import DeliveryTemplate, DraftSnapshot
from relay.services.delivery_workbook import delivery_workbook
from relay.services.schedule_delivery import SCHEDULE_OUTPUTS

_WORKBOOK_LIMIT = 10 * 1024 * 1024

RELEASE_COLUMNS = """
    r.id, r.run_id, r.version, r.draft_revision, r.rule_version, r.output_version,
    r.template, r.ship_through, r.retained_issues, r.document_no, r.filename, r.confirmed_at,
    u.username AS confirmed_by
"""


class ReleaseBlocker(BaseModel):
    kind: str
    customer_item: str | None
    order_no: str | None
    cell: str | None
    requested_date: date | None = None
    reason: str | None = None


class ReleasePreview(BaseModel):
    pending_mappings: int
    pending_reviews: int
    pending_shipments: int
    future_unscheduled: int
    scheduled: int
    blockers: list[ReleaseBlocker]
    calculated_quantities: int
    retained_issues: int
    retained_details: list[ReleaseBlocker]

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
    template: DeliveryTemplate
    ship_through: date | None
    retained_issues: int
    document_no: str | None
    filename: str
    confirmed_at: datetime
    confirmed_by: str


def check_release(draft: DraftSnapshot, ship_through: date | None = None) -> ReleasePreview:
    result = ReleasePreview(
        pending_mappings=0,
        pending_reviews=0,
        pending_shipments=0,
        future_unscheduled=0,
        scheduled=0,
        blockers=[],
        calculated_quantities=0,
        retained_issues=0,
        retained_details=[],
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
            if draft.template != "drx_delfor":
                plan = entry.plan
                if plan and plan.kind in {"day", "week"} and plan.amount is not None:
                    result.calculated_quantities += 1
                if plan is None or plan.kind == "review":
                    details = result.blockers
                    result.pending_reviews += 1
                elif plan.issue:
                    details = result.retained_details
                    result.retained_issues += 1
                else:
                    continue
                if len(details) < 20:
                    details.append(
                        ReleaseBlocker(
                            kind="schedule_review",
                            customer_item=material.customer_item,
                            order_no=material.order_no,
                            cell=entry.quantity_cell,
                            requested_date=plan.start if plan else None,
                            reason=plan.issue if plan else "缺少计算结果，请重新匹配并核验",
                        )
                    )
                continue
            kind: str
            required_date = (
                entry.review.delivery_date if entry.review else None
            ) or entry.requested_date
            if entry.state in {"code_review", "period_review"}:
                result.pending_reviews += 1
                kind = entry.state
            elif entry.state == "transport_missing":
                if (
                    ship_through is not None
                    and required_date is not None
                    and required_date > ship_through
                ):
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
    ship_through: date | None,
    retained_issues: int,
    actor_id: int,
) -> DeliveryRelease:
    document_no = re.sub(r"[^A-Za-z0-9_-]+", "-", (draft.document_no or str(run_id)))[:48]
    title = "德科" if draft.template == "drx_delfor" else SCHEDULE_OUTPUTS[draft.template].title
    filename = f"{title}交付计划_报文{document_no}_V{version}.xlsx"
    workbook = delivery_workbook(draft, release_version=version, ship_through=ship_through)
    if len(workbook) > _WORKBOOK_LIMIT:
        raise ValueError("release_workbook_too_large")
    connection.execute(
        text("""
            INSERT INTO delivery_releases (
                run_id, version, draft_revision, template, ship_through, retained_issues,
                rule_version, output_version,
                document_no, filename, workbook, checksum, confirmed_by
            ) VALUES (
                :run_id, :version, :revision, :template, :ship_through, :retained_issues,
                :rule_version, :output_version,
                :document_no, :filename, :workbook, :checksum, :actor_id
            )
        """),
        {
            "run_id": run_id,
            "version": version,
            "revision": revision,
            "template": draft.template,
            "ship_through": ship_through,
            "retained_issues": retained_issues,
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
