from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Connection, text

from relay.services.delivery import DeliveryLeadTimes, DraftSnapshot, ShipmentInput

Transport = Literal["air", "sea"]


class EtaChange(BaseModel):
    mode: Transport
    ship_date: date
    before: date
    after: date
    customer_item: str | None = None
    quantity_cell: str | None = None


class RuleImpact(BaseModel):
    attachment_id: int
    filename: str
    document_no: str | None
    parser_version: str
    sample_revision: int | None = None
    material_count: int
    quantity_count: int
    pending_entries: int
    pending_shipments: int
    changed_eta_count: int
    changed_etas: list[EtaChange]
    simulated_etas: list[EtaChange]


class DrxRuleVersion(BaseModel):
    id: int
    version: str
    parameters: DeliveryLeadTimes
    source_note: str
    baseline_id: int | None
    trial: RuleImpact | None
    created_at: datetime
    created_by: str | None
    published_at: datetime | None
    published_by: str | None


_RULES = """
    SELECT r.id, 'drx-' || r.id AS version,
           jsonb_build_object('air_days', r.air_days, 'sea_days', r.sea_days) AS parameters,
           r.source_note, r.baseline_id, r.trial, r.created_at, author.username AS created_by,
           r.published_at, publisher.username AS published_by
    FROM drx_rule_versions AS r
    LEFT JOIN relay_users AS author ON author.id = r.created_by
    LEFT JOIN relay_users AS publisher ON publisher.id = r.published_by
"""


def active_rule(connection: Connection) -> DrxRuleVersion:
    row = (
        connection.execute(
            text(
                _RULES + " WHERE r.published_at IS NOT NULL "
                "ORDER BY r.published_at DESC, r.id DESC LIMIT 1"
            )
        )
        .mappings()
        .one()
    )
    return DrxRuleVersion.model_validate(row)


def rule_versions(connection: Connection, *, published_only: bool = False) -> list[DrxRuleVersion]:
    where = " WHERE r.published_at IS NOT NULL" if published_only else ""
    return [
        DrxRuleVersion.model_validate(row)
        for row in connection.execute(
            text(_RULES + where + " ORDER BY r.id DESC LIMIT 100")
        ).mappings()
    ]


def rule_by_id(connection: Connection, version_id: int) -> DrxRuleVersion | None:
    row = (
        connection.execute(text(_RULES + " WHERE r.id = :id"), {"id": version_id})
        .mappings()
        .one_or_none()
    )
    return DrxRuleVersion.model_validate(row) if row is not None else None


def compare_lead_times(
    draft: DraftSnapshot,
    baseline: DeliveryLeadTimes,
    candidate: DeliveryLeadTimes,
    *,
    parser_version: str,
    simulated_date: date | None = None,
    sample_revision: int | None = None,
) -> RuleImpact:
    changes: list[EtaChange] = []
    changed_count = 0
    for material in draft.materials:
        for entry in material.entries:
            shipment = entry.shipment
            if shipment is None:
                continue
            before, after = shipment.arrival(baseline), shipment.arrival(candidate)
            if before != after:
                changed_count += 1
                if len(changes) < 20:
                    changes.append(
                        EtaChange(
                            mode=shipment.mode,
                            ship_date=shipment.ship_date,
                            before=before,
                            after=after,
                            customer_item=material.customer_item,
                            quantity_cell=entry.quantity_cell,
                        )
                    )
    simulated = []
    if simulated_date is not None:
        modes: tuple[Transport, ...] = ("air", "sea")
        for mode in modes:
            shipment_input = ShipmentInput(
                ship_date=simulated_date, mode=mode, source_note="试算发货日"
            )
            simulated.append(
                EtaChange(
                    mode=mode,
                    ship_date=simulated_date,
                    before=shipment_input.arrival(baseline),
                    after=shipment_input.arrival(candidate),
                )
            )
    return RuleImpact(
        attachment_id=draft.attachment_id,
        filename=draft.filename,
        document_no=draft.document_no,
        parser_version=parser_version,
        sample_revision=sample_revision,
        material_count=len(draft.materials),
        quantity_count=sum(len(material.entries) for material in draft.materials),
        pending_entries=draft.pending_entries,
        pending_shipments=draft.pending_shipments,
        changed_eta_count=changed_count,
        changed_etas=changes,
        simulated_etas=simulated,
    )
