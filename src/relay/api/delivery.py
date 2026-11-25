from collections import defaultdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from relay.api.auth import AdminUser, SignedInUser, require_browser_request
from relay.database.session import get_engine
from relay.services.delivery import (
    RULE_VERSION,
    Customer,
    DraftEntry,
    DraftMaterial,
    DraftSnapshot,
    MappingEdit,
    MappingValues,
    MaterialMapping,
    Shipment,
    ShipmentInput,
    demand_period,
    entry_state,
    find_drx_mapping,
    matching_order,
)
from relay.services.delivery_xlsx import build_workbook

router = APIRouter(prefix="/api/v1", tags=["delivery"])
_BROWSER = [Depends(require_browser_request)]
_REVISION = Annotated[int, Field(ge=1)]
_MAPPING_FIELDS = ", ".join(MappingValues.model_fields)
_MAPPING_COLUMNS = f"{_MAPPING_FIELDS}, order_key"
DRAFT_REVIEW_PREDICATE = (
    "((snapshot->>'pending_mappings')::integer > 0 OR (snapshot->>'pending_entries')::integer > 0)"
)
_ATTACHMENT = """
    SELECT run.id, run.source_fields, att.id AS attachment_id, att.mail_id, att.filename
    FROM parse_runs AS run
    JOIN parse_jobs AS job ON job.id = run.job_id
    JOIN received_attachments AS att ON att.id = job.attachment_id
    WHERE run.template = 'drx_delfor'
"""


class DraftSummary(BaseModel):
    id: int
    attachment_id: int
    filename: str
    document_no: str | None
    document_date: str | None
    pending_mappings: int
    pending_entries: int
    updated_at: datetime


class DeliveryDraft(DraftSnapshot):
    id: int
    revision: int


class RefreshDraft(BaseModel):
    revision: _REVISION


class ChangeShipment(RefreshDraft):
    shipment: ShipmentInput | None


@router.get("/material-mappings")
def list_mappings(
    _: AdminUser,
    q: Annotated[str, Query(max_length=120)] = "",
    customer: Customer | None = None,
    before: Annotated[int, Query(gt=0)] | None = None,
) -> list[MaterialMapping]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                f"SELECT id, revision, {_MAPPING_FIELDS} FROM material_mappings "
                "WHERE (CAST(:customer AS text) IS NULL OR customer = :customer) "
                "AND (CAST(:before AS bigint) IS NULL OR id < :before) "
                "AND (:q = '' OR strpos(lower(concat_ws(' ', customer_item, "
                "internal_item, supplier_item, order_no, order_line, ship_to)), lower(:q)) > 0) "
                "ORDER BY id DESC LIMIT 100"
            ),
            {"customer": customer, "before": before, "q": q.strip()},
        ).mappings()
        return [MaterialMapping.model_validate(row) for row in rows]


def _save_mapping(
    values: MappingValues, *, mapping_id: int | None = None, revision: int | None = None
) -> MaterialMapping:
    data = values.model_dump(include=set(MappingValues.model_fields))
    data["order_key"] = matching_order(values.customer, values.order_no)
    columns = _MAPPING_COLUMNS.split(", ")
    if mapping_id is None:
        fields = ", ".join(f":{field}" for field in columns)
        statement = f"INSERT INTO material_mappings ({_MAPPING_COLUMNS}) VALUES ({fields})"
    else:
        fields = ", ".join(f"{field} = :{field}" for field in columns)
        statement = (
            f"UPDATE material_mappings SET {fields}, revision = revision + 1 "
            "WHERE id = :id AND revision = :revision"
        )
        data.update(id=mapping_id, revision=revision)
    try:
        with get_engine().begin() as connection:
            row = (
                connection.execute(
                    text(f"{statement} RETURNING id, revision, {_MAPPING_FIELDS}"),
                    data,
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise HTTPException(409, detail="mapping_changed")
            return MaterialMapping.model_validate(row)
    except IntegrityError as error:
        if getattr(getattr(error.orig, "diag", None), "constraint_name", None) != (
            "uq_material_mapping_scope"
        ):
            raise
        raise HTTPException(409, detail="mapping_scope_exists") from None


@router.post("/material-mappings", dependencies=_BROWSER, status_code=201)
def add_mapping(values: MappingValues, _: AdminUser) -> MaterialMapping:
    return _save_mapping(values)


@router.put("/material-mappings/{mapping_id}", dependencies=_BROWSER)
def edit_mapping(mapping_id: int, values: MappingEdit, _: AdminUser) -> MaterialMapping:
    return _save_mapping(values, mapping_id=mapping_id, revision=values.revision)


def _snapshot(row: RowMapping) -> DeliveryDraft:
    return DeliveryDraft.model_validate(
        {**row["snapshot"], "id": row["run_id"], "revision": row["revision"]}
    )


def _counts(materials: list[DraftMaterial]) -> tuple[int, int]:
    return (
        sum(material.mapping_state != "matched" for material in materials),
        sum(
            entry.state not in {"scheduled", "recorded_zero"}
            for material in materials
            for entry in material.entries
        ),
    )


def _build_drx_snapshot(
    connection: Connection, source: RowMapping, previous: DeliveryDraft | None = None
) -> DraftSnapshot:
    previous_shipments = {
        entry.id: entry.shipment
        for material in (previous.materials if previous else [])
        for entry in material.entries
        if entry.shipment is not None
    }
    blocks = (
        connection.execute(
            text(
                "SELECT block.id, "
                "block.source_fields->'BuyerItemNo'->>'value' AS customer_item, "
                "block.source_fields->'BuyerItemNo'->>'cell' AS customer_item_cell, "
                "block.source_fields->'poNo'->>'value' AS order_no, "
                "block.source_fields->'POLineNo'->>'value' AS order_line, "
                "block.source_fields->'ShipToNo'->>'value' AS ship_to, "
                "block.source_fields->'SupplierItemNumber'->>'value' AS supplier_item "
                "FROM parsed_material_blocks AS block "
                "WHERE block.run_id = :id ORDER BY block.position"
            ),
            {"id": source["id"]},
        )
        .mappings()
        .all()
    )
    materials_by_item: dict[str, list[MaterialMapping]] = defaultdict(list)
    for row in connection.execute(
        text(
            f"SELECT id, revision, {_MAPPING_FIELDS} FROM material_mappings "
            "WHERE customer = 'drx' AND customer_item = ANY(:items)"
        ),
        {"items": list({block["customer_item"] for block in blocks if block["customer_item"]})},
    ).mappings():
        mapping = MaterialMapping.model_validate(row)
        materials_by_item[mapping.customer_item].append(mapping)

    entries: dict[int, list[DraftEntry]] = defaultdict(list)
    records = connection.execute(
        text(
            "SELECT qty.id, qty.block_id, qty.quantity, qty.requested_date, "
            "qty.earliest_date, qty.latest_date, "
            "qty.source_fields->'QtyType'->>'value' AS code, "
            "qty.source_fields->'QtyUnit'->>'value' AS unit, "
            "qty.source_fields->'DeliveryPlanStatusIndicator'->>'value' AS status_code, "
            "qty.source_fields->'Qty'->>'value' AS raw_quantity, "
            "qty.source_fields->'Qty'->>'cell' AS quantity_cell, "
            "CASE WHEN qty.requested_date IS NOT NULL "
            "THEN qty.source_fields->'RequestedDeliveryDate'->>'cell' "
            "ELSE qty.source_fields->'HorizonStartDate'->>'cell' END AS date_cell, "
            "CASE WHEN qty.latest_date IS NOT NULL "
            "THEN qty.source_fields->'HorizonEndDate'->>'cell' END AS end_date_cell "
            "FROM parsed_quantity_records AS qty "
            "JOIN parsed_material_blocks AS block ON block.id = qty.block_id "
            "WHERE block.run_id = :id ORDER BY qty.position"
        ),
        {"id": source["id"]},
    ).mappings()
    for record in records:
        state = entry_state(
            record["code"],
            record["unit"],
            record["status_code"],
            record["requested_date"],
            record["quantity"],
        )
        shipment = previous_shipments.get(record["id"])
        if shipment is not None and state == "transport_missing":
            state = "scheduled"
        else:
            shipment = None
        entries[record["block_id"]].append(
            DraftEntry.model_validate(
                {
                    **record,
                    "period": demand_period(
                        record["requested_date"], record["earliest_date"], record["latest_date"]
                    ),
                    "state": state,
                    "shipment": shipment,
                    "eta": shipment.arrival() if shipment else None,
                }
            )
        )
    result = []
    for block in blocks:
        mapping_state, selected_mapping = find_drx_mapping(
            materials_by_item.get(block["customer_item"] or "", []),
            item=block["customer_item"],
            order=block["order_no"],
            order_line=block["order_line"],
            ship_to=block["ship_to"],
            supplier=block["supplier_item"],
        )
        result.append(
            DraftMaterial.model_validate(
                {
                    **block,
                    "mapping": selected_mapping,
                    "mapping_state": mapping_state,
                    "entries": entries[block["id"]],
                }
            )
        )
    fields = source["source_fields"]
    pending_mappings, pending_entries = _counts(result)
    return DraftSnapshot(
        rule_version=RULE_VERSION,
        attachment_id=source["attachment_id"],
        mail_id=source["mail_id"],
        filename=source["filename"],
        document_no=fields.get("DelforNo", {}).get("value"),
        document_date=fields.get("DelforCreateDate", {}).get("value"),
        materials=result,
        pending_mappings=pending_mappings,
        pending_entries=pending_entries,
    )


def _draft_row(connection: Connection, run_id: int, *, lock: bool = False) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT * FROM delivery_drafts WHERE run_id = :id" + (" FOR UPDATE" if lock else "")
            ),
            {"id": run_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, detail="draft_not_found")
    return row


def _check_revision(draft: DeliveryDraft, revision: int) -> None:
    if draft.revision != revision:
        raise HTTPException(409, detail="draft_changed")


def _replace_draft(
    connection: Connection, run_id: int, snapshot: DraftSnapshot, actor_id: int
) -> DeliveryDraft:
    row = (
        connection.execute(
            text(
                "UPDATE delivery_drafts SET revision = revision + 1, snapshot = :snapshot, "
                "updated_at = now(), updated_by = :actor WHERE run_id = :id RETURNING *"
            ),
            {
                "snapshot": Jsonb(
                    snapshot.model_dump(mode="json", include=set(DraftSnapshot.model_fields))
                ),
                "id": run_id,
                "actor": actor_id,
            },
        )
        .mappings()
        .one()
    )
    return _snapshot(row)


@router.get("/delivery")
def list_drafts(
    _: SignedInUser,
    before: Annotated[int, Query(gt=0)] | None = None,
    review: bool = False,
    q: Annotated[str, Query(max_length=120)] = "",
) -> list[DraftSummary]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT run_id AS id, updated_at, "
                "snapshot->>'attachment_id' AS attachment_id, snapshot->>'filename' AS filename, "
                "snapshot->>'document_no' AS document_no, "
                "snapshot->>'document_date' AS document_date, "
                "snapshot->>'pending_mappings' AS pending_mappings, "
                "snapshot->>'pending_entries' AS pending_entries FROM delivery_drafts "
                "WHERE (CAST(:before AS bigint) IS NULL OR run_id < :before) "
                "AND (:q = '' OR strpos(lower(concat_ws(' ', snapshot->>'filename', "
                "snapshot->>'document_no', '德科 DRX')), lower(:q)) > 0) "
                f"AND (NOT :review OR {DRAFT_REVIEW_PREDICATE}) "
                "ORDER BY run_id DESC LIMIT 100"
            ),
            {"before": before, "review": review, "q": q.strip()},
        ).mappings()
        return [DraftSummary.model_validate(row) for row in rows]


@router.post("/delivery/attachments/{attachment_id}", dependencies=_BROWSER)
def open_draft(attachment_id: int, actor: AdminUser) -> DeliveryDraft:
    with get_engine().begin() as connection:
        source = (
            connection.execute(text(_ATTACHMENT + " AND att.id = :id"), {"id": attachment_id})
            .mappings()
            .one_or_none()
        )
        if source is None:
            raise HTTPException(404, detail="delivery_source_unavailable")
        run_id = source["id"]
        existing = (
            connection.execute(
                text("SELECT * FROM delivery_drafts WHERE run_id = :id"), {"id": run_id}
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            return _snapshot(existing)
        draft = _build_drx_snapshot(connection, source)
        connection.execute(
            text(
                "INSERT INTO delivery_drafts (run_id, snapshot, updated_by) "
                "VALUES (:id, :snapshot, :actor) ON CONFLICT (run_id) DO NOTHING"
            ),
            {"id": run_id, "snapshot": Jsonb(draft.model_dump(mode="json")), "actor": actor.id},
        )
        return _snapshot(_draft_row(connection, run_id))


@router.get("/delivery/{run_id}")
def read_draft(run_id: int, _: SignedInUser) -> DeliveryDraft:
    with get_engine().connect() as connection:
        return _snapshot(_draft_row(connection, run_id))


@router.post("/delivery/{run_id}/refresh", dependencies=_BROWSER)
def refresh_draft(run_id: int, request: RefreshDraft, actor: AdminUser) -> DeliveryDraft:
    with get_engine().begin() as connection:
        previous = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(previous, request.revision)
        source = (
            connection.execute(text(_ATTACHMENT + " AND run.id = :id"), {"id": run_id})
            .mappings()
            .one()
        )
        snapshot = _build_drx_snapshot(connection, source, previous)
        return _replace_draft(connection, run_id, snapshot, actor.id)


@router.put("/delivery/{run_id}/shipments/{entry_id}", dependencies=_BROWSER)
def change_shipment(
    run_id: int, entry_id: int, request: ChangeShipment, actor: AdminUser
) -> DeliveryDraft:
    with get_engine().begin() as connection:
        draft = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(draft, request.revision)
        match = next(
            (
                (material, entry)
                for material in draft.materials
                for entry in material.entries
                if entry.id == entry_id
            ),
            None,
        )
        if match is None:
            raise HTTPException(404, detail="delivery_entry_not_found")
        material, entry = match
        if material.mapping_state != "matched" or entry.state not in {
            "transport_missing",
            "scheduled",
        }:
            raise HTTPException(409, detail="delivery_entry_requires_review")
        entry.shipment = (
            Shipment(**request.shipment.model_dump(), entered_by=actor.username)
            if request.shipment is not None
            else None
        )
        entry.state = "scheduled" if entry.shipment else "transport_missing"
        entry.eta = entry.shipment.arrival() if entry.shipment else None
        draft.pending_mappings, draft.pending_entries = _counts(draft.materials)
        return _replace_draft(connection, run_id, draft, actor.id)


@router.get("/delivery/{run_id}/draft.xlsx")
def download_draft(run_id: int, _: SignedInUser, revision: Annotated[int, Query(ge=1)]) -> Response:
    with get_engine().connect() as connection:
        draft = _snapshot(_draft_row(connection, run_id))
    _check_revision(draft, revision)
    return Response(
        build_workbook(draft),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="Relay-DRX-{run_id}-draft.xlsx"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
