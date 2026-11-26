from collections import defaultdict
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import IntegrityError

from relay.api.auth import AdminUser, SignedInUser, require_browser_request
from relay.auth.accounts import User
from relay.database.session import get_engine
from relay.services.delivery import (
    DELIVERY_CUSTOMERS,
    DRX_OUTPUT_VERSION,
    RULE_VERSION,
    Customer,
    DeliveryLeadTimes,
    DeliveryTemplate,
    DraftEntry,
    DraftMaterial,
    DraftSnapshot,
    EntryReview,
    EntryReviewInput,
    EntryReviewKind,
    MappingEdit,
    MappingValues,
    MaterialCandidate,
    MaterialMapping,
    Shipment,
    ShipmentInput,
    apply_entry_review,
    demand_period,
    entry_state,
    find_material_mapping,
    matching_order,
    review_kind,
    valid_entry_review,
)
from relay.services.delivery_release import (
    DeliveryRelease,
    ReleasePreview,
    check_release,
    freeze_release,
    list_releases,
)
from relay.services.delivery_rules import (
    DrxRuleVersion,
    RuleImpact,
    active_rule,
    compare_lead_times,
    rule_by_id,
    rule_versions,
)
from relay.services.delivery_workbook import delivery_workbook
from relay.services.schedule_delivery import build_schedule_snapshot
from relay.services.template_catalog import WORKBOOK_TEMPLATES, WorkbookTemplate

router = APIRouter(prefix="/api/v1", tags=["delivery"])
_BROWSER = [Depends(require_browser_request)]
_REVISION = Annotated[int, Field(ge=1)]
_MAPPING_FIELDS = ", ".join(MappingValues.model_fields)
_MAPPING_COLUMNS = f"{_MAPPING_FIELDS}, order_key"
_CURRENT_RELEASE = """SELECT version FROM delivery_releases
    WHERE run_id = delivery_drafts.run_id AND draft_revision = delivery_drafts.revision"""
DRAFT_REVIEW_PREDICATE = f"""(
    ((snapshot->>'pending_mappings')::integer > 0 OR (snapshot->>'pending_entries')::integer > 0)
    AND NOT EXISTS ({_CURRENT_RELEASE})
)"""
_ATTACHMENT = """
    SELECT run.id, run.template, run.source_fields, run.parser_version,
           att.id AS attachment_id, att.mail_id, att.filename,
           CAST(mail.received_at AT TIME ZONE 'Asia/Shanghai' AS date) AS received_day
    FROM parse_runs AS run
    JOIN parse_jobs AS job ON job.id = run.job_id
    JOIN received_attachments AS att ON att.id = job.attachment_id
    JOIN received_mail AS mail ON mail.id = att.mail_id
"""


class DraftSummary(BaseModel):
    id: int
    template: DeliveryTemplate
    attachment_id: int
    filename: str
    document_no: str | None
    document_date: str | None
    pending_mappings: int
    pending_entries: int
    pending_shipments: int
    updated_at: datetime
    confirmed_version: int | None


class DeliveryDraft(DraftSnapshot):
    id: int
    revision: int


class RefreshDraft(BaseModel):
    revision: _REVISION


class ConfirmRelease(RefreshDraft):
    ship_through: date | None = None
    acknowledge_retained: bool = False


class ChangeShipment(RefreshDraft):
    shipment: ShipmentInput | None


class ConfirmReview(RefreshDraft):
    review: EntryReviewInput


class ConfirmBatchReview(ConfirmReview):
    entry_ids: Annotated[list[int], Field(min_length=2, max_length=5000)]


class RuleSample(BaseModel):
    attachment_id: int
    filename: str
    document_no: str | None
    document_date: str | None
    parser_version: str


class DrxRules(BaseModel):
    active: DrxRuleVersion
    versions: list[DrxRuleVersion]
    samples: list[RuleSample]


class TryRule(BaseModel):
    baseline_id: int
    attachment_id: int
    parameters: DeliveryLeadTimes
    simulated_date: Annotated[date, Field(ge=date(1900, 1, 1), le=date(9998, 12, 31))]
    source_note: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
    ]


class DraftRulePreview(BaseModel):
    target: DrxRuleVersion
    impact: RuleImpact
    revision: int


class ApplyRule(RefreshDraft):
    target_id: int


class DraftRuleHistory(BaseModel):
    revision: int
    rule_version: str
    replaced_at: datetime


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


@router.get("/material-mappings/candidates")
def list_material_candidates(
    _: AdminUser,
    q: Annotated[str, Query(max_length=120)] = "",
    customer: Customer | None = None,
    before: Annotated[int, Query(gt=0)] | None = None,
) -> list[MaterialCandidate]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text("""
                SELECT c.id, c.customer, c.customer_item, c.order_no, c.order_line, c.ship_to,
                       c.supplier_item, c.source_filename, c.source_cell, c.source_date
                FROM material_candidates AS c
                WHERE (CAST(:customer AS text) IS NULL OR c.customer = :customer)
                  AND (CAST(:before AS bigint) IS NULL OR c.id < :before)
                  AND (:q = '' OR strpos(lower(concat_ws(' ', c.customer_item, c.supplier_item,
                        c.order_no, c.order_line, c.ship_to)), lower(:q)) > 0)
                  AND NOT (
                      SELECT count(*) = 1
                          AND bool_and(best.supplier_item IS NULL
                              OR best.supplier_item IS NOT DISTINCT FROM c.supplier_item)
                      FROM (
                          SELECT m.supplier_item
                          FROM material_mappings AS m
                          WHERE m.customer = c.customer AND m.customer_item = c.customer_item
                            AND (m.order_key IS NULL OR m.order_key = c.order_key)
                            AND (m.order_line IS NULL OR m.order_line = c.order_line)
                            AND (m.ship_to IS NULL OR m.ship_to = c.ship_to)
                          ORDER BY (m.order_key IS NOT NULL)::int + (m.order_line IS NOT NULL)::int
                              + (m.ship_to IS NOT NULL)::int DESC
                          FETCH FIRST 1 ROW WITH TIES
                      ) AS best
                  )
                ORDER BY c.id DESC LIMIT 100
            """),
            {"customer": customer, "before": before, "q": q.strip()},
        ).mappings()
        return [MaterialCandidate.model_validate(row) for row in rows]


@router.delete(
    "/material-mappings/candidates/{candidate_id}", dependencies=_BROWSER, status_code=204
)
def delete_material_candidate(candidate_id: int, _: AdminUser) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text("DELETE FROM material_candidates WHERE id = :id"), {"id": candidate_id}
        )


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


@router.delete("/material-mappings/{mapping_id}", dependencies=_BROWSER, status_code=204)
def delete_mapping(mapping_id: int, revision: Annotated[int, Query(ge=1)], _: AdminUser) -> None:
    with get_engine().begin() as connection:
        deleted = connection.execute(
            text("DELETE FROM material_mappings WHERE id = :id AND revision = :revision"),
            {"id": mapping_id, "revision": revision},
        ).rowcount
        if deleted == 0 and connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM material_mappings WHERE id = :id)"),
            {"id": mapping_id},
        ):
            raise HTTPException(409, detail="mapping_changed")


def _snapshot(row: RowMapping) -> DeliveryDraft:
    return DeliveryDraft.model_validate(
        {**row["snapshot"], "id": row["run_id"], "revision": row["revision"]}
    )


def _counts(materials: list[DraftMaterial]) -> tuple[int, int, int]:
    return (
        sum(material.mapping_state != "matched" for material in materials),
        sum(
            entry.state in {"code_review", "period_review"}
            for material in materials
            for entry in material.entries
        ),
        sum(
            entry.state == "transport_missing"
            for material in materials
            for entry in material.entries
        ),
    )


def _recount(draft: DraftSnapshot) -> None:
    draft.pending_mappings, draft.pending_entries, draft.pending_shipments = _counts(
        draft.materials
    )


def _shipment_eta(shipment: ShipmentInput, parameters: DeliveryLeadTimes) -> date:
    try:
        return shipment.arrival(parameters)
    except OverflowError:
        raise HTTPException(422, detail="delivery_eta_out_of_range") from None


def _build_drx_snapshot(
    connection: Connection,
    source: RowMapping,
    previous: DeliveryDraft | None = None,
    *,
    rule: DrxRuleVersion | None = None,
) -> DraftSnapshot:
    parameters = (
        previous.rule_parameters if previous else (rule.parameters if rule else DeliveryLeadTimes())
    )
    previous_entries = {
        entry.id: entry
        for material in (previous.materials if previous else [])
        for entry in material.entries
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
            record["earliest_date"],
            record["latest_date"],
        )
        entry = DraftEntry.model_validate(
            {
                **record,
                "period": demand_period(
                    record["requested_date"], record["earliest_date"], record["latest_date"]
                ),
                "state": state,
            }
        )
        prior = previous_entries.get(entry.id)
        if prior and prior.review and prior.review.kind == review_kind(entry):
            if valid_entry_review(entry, prior.review.kind, prior.review):
                apply_entry_review(entry, prior.review)
        if prior and prior.shipment and entry.state == "transport_missing":
            entry.state = "scheduled"
            entry.shipment = prior.shipment
            entry.eta = _shipment_eta(prior.shipment, parameters)
        entries[record["block_id"]].append(entry)
    result = []
    for block in blocks:
        mapping_state, selected_mapping = find_material_mapping(
            materials_by_item.get(block["customer_item"] or "", []),
            customer="drx",
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
    pending_mappings, pending_entries, pending_shipments = _counts(result)
    return DraftSnapshot(
        rule_version=previous.rule_version
        if previous
        else (rule.version if rule else RULE_VERSION),
        rule_parameters=parameters,
        output_version=previous.output_version if previous else DRX_OUTPUT_VERSION,
        attachment_id=source["attachment_id"],
        mail_id=source["mail_id"],
        filename=source["filename"],
        document_no=fields.get("DelforNo", {}).get("value"),
        document_date=fields.get("DelforCreateDate", {}).get("value"),
        materials=result,
        pending_mappings=pending_mappings,
        pending_entries=pending_entries,
        pending_shipments=pending_shipments,
    )


def _draft_row(connection: Connection, run_id: int, *, lock: bool = False) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT * FROM delivery_drafts WHERE run_id = :id AND deleted_at IS NULL"
                + (" FOR UPDATE" if lock else "")
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


def _require_drx(draft: DeliveryDraft) -> None:
    if draft.template != "drx_delfor":
        raise HTTPException(409, detail="drx_operation_only")


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
    confirmed: bool = False,
) -> list[DraftSummary]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT run_id AS id, updated_at, "
                f"({_CURRENT_RELEASE}) AS confirmed_version, "
                "COALESCE(snapshot->>'template', 'drx_delfor') AS template, "
                "snapshot->>'attachment_id' AS attachment_id, snapshot->>'filename' AS filename, "
                "snapshot->>'document_no' AS document_no, "
                "snapshot->>'document_date' AS document_date, "
                "snapshot->>'pending_mappings' AS pending_mappings, "
                "snapshot->>'pending_entries' AS pending_entries, "
                "COALESCE((snapshot->>'pending_shipments')::integer, 0) AS pending_shipments "
                "FROM delivery_drafts "
                "WHERE deleted_at IS NULL "
                "AND (CAST(:before AS bigint) IS NULL OR run_id < :before) "
                "AND (:q = '' OR strpos(lower(concat_ws(' ', snapshot->>'filename', "
                "snapshot->>'document_no', CASE snapshot->>'template' "
                "WHEN 'kroschu_4905' THEN '科伯舒特 Kroschu 4905' "
                "WHEN 'vitesco_delfor' THEN '舍弗勒 Vitesco DELFOR' "
                "WHEN 'jlr_delfor' THEN '捷豹路虎 JLR DELFOR' "
                "WHEN 'jlr_deljit' THEN '捷豹路虎 JLR DELJIT' ELSE '德科 DRX' END)), "
                "lower(:q)) > 0) "
                f"AND (NOT :review OR {DRAFT_REVIEW_PREDICATE}) "
                f"AND (NOT :confirmed OR EXISTS ({_CURRENT_RELEASE})) "
                "ORDER BY run_id DESC LIMIT 100"
            ),
            {"before": before, "review": review, "confirmed": confirmed, "q": q.strip()},
        ).mappings()
        return [DraftSummary.model_validate(row) for row in rows]


@router.post("/delivery/attachments/{attachment_id}", dependencies=_BROWSER)
def open_draft(attachment_id: int, actor: AdminUser) -> DeliveryDraft:
    with get_engine().begin() as connection:
        source = _delivery_source(connection, attachment_id=attachment_id)
        run_id = source["id"]
        existing = (
            connection.execute(
                text("SELECT * FROM delivery_drafts WHERE run_id = :id AND deleted_at IS NULL"),
                {"id": run_id},
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            return _snapshot(existing)
        draft = (
            _build_drx_snapshot(connection, source, rule=active_rule(connection))
            if source.template == "drx_delfor"
            else build_schedule_snapshot(connection, source)
        )
        connection.execute(
            text(
                "INSERT INTO delivery_drafts (run_id, snapshot, updated_by) "
                "VALUES (:id, :snapshot, :actor) ON CONFLICT (run_id) DO UPDATE "
                "SET revision = delivery_drafts.revision + 1, snapshot = excluded.snapshot, "
                "updated_at = now(), updated_by = excluded.updated_by, deleted_at = NULL "
                "WHERE delivery_drafts.deleted_at IS NOT NULL"
            ),
            {"id": run_id, "snapshot": Jsonb(draft.model_dump(mode="json")), "actor": actor.id},
        )
        return _snapshot(_draft_row(connection, run_id))


@router.get("/delivery/{run_id}")
def read_draft(run_id: int, _: SignedInUser) -> DeliveryDraft:
    with get_engine().connect() as connection:
        return _snapshot(_draft_row(connection, run_id))


@router.delete("/delivery/{run_id}", dependencies=_BROWSER, status_code=204)
def delete_draft(run_id: int, revision: Annotated[int, Query(ge=1)], actor: AdminUser) -> None:
    with get_engine().begin() as connection:
        draft = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(draft, revision)
        if connection.scalar(
            text("SELECT 1 FROM delivery_releases WHERE run_id = :id LIMIT 1"), {"id": run_id}
        ):
            raise HTTPException(409, detail="draft_has_confirmed_release")
        connection.execute(
            text("DELETE FROM delivery_draft_rule_history WHERE run_id = :id"), {"id": run_id}
        )
        connection.execute(
            text("""
                UPDATE delivery_drafts
                SET deleted_at = now(), snapshot = '{}', revision = revision + 1,
                    updated_at = now(), updated_by = :actor
                WHERE run_id = :id
            """),
            {"id": run_id, "actor": actor.id},
        )


@router.post("/delivery/{run_id}/refresh", dependencies=_BROWSER)
def refresh_draft(run_id: int, request: RefreshDraft, actor: AdminUser) -> DeliveryDraft:
    with get_engine().begin() as connection:
        previous = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(previous, request.revision)
        source = (
            connection.execute(text(_ATTACHMENT + " WHERE run.id = :id"), {"id": run_id})
            .mappings()
            .one()
        )
        snapshot = (
            _build_drx_snapshot(connection, source, previous)
            if source.template == "drx_delfor"
            else build_schedule_snapshot(connection, source)
        )
        return _replace_draft(connection, run_id, snapshot, actor.id)


@router.put("/delivery/{run_id}/shipments/{entry_id}", dependencies=_BROWSER)
def change_shipment(
    run_id: int, entry_id: int, request: ChangeShipment, actor: AdminUser
) -> DeliveryDraft:
    with get_engine().begin() as connection:
        draft = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(draft, request.revision)
        _require_drx(draft)
        material, entry = _draft_entry(draft, entry_id)
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
        entry.eta = _shipment_eta(entry.shipment, draft.rule_parameters) if entry.shipment else None
        _recount(draft)
        return _replace_draft(connection, run_id, draft, actor.id)


def _draft_entry(draft: DeliveryDraft, entry_id: int) -> tuple[DraftMaterial, DraftEntry]:
    for material in draft.materials:
        for entry in material.entries:
            if entry.id == entry_id:
                return material, entry
    raise HTTPException(404, detail="delivery_entry_not_found")


@router.put("/delivery/{run_id}/reviews/{entry_id}", dependencies=_BROWSER)
def confirm_review(
    run_id: int, entry_id: int, request: ConfirmReview, actor: AdminUser
) -> DeliveryDraft:
    with get_engine().begin() as connection:
        draft = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(draft, request.revision)
        _require_drx(draft)
        _, entry = _draft_entry(draft, entry_id)
        kind = review_kind(entry)
        if kind is None or entry.shipment is not None:
            raise HTTPException(409, detail="delivery_review_unavailable")
        return _save_reviews(connection, draft, [entry], kind, request.review, actor)


@router.put("/delivery/{run_id}/reviews", dependencies=_BROWSER)
def confirm_batch_review(
    run_id: int, request: ConfirmBatchReview, actor: AdminUser
) -> DeliveryDraft:
    with get_engine().begin() as connection:
        draft = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(draft, request.revision)
        _require_drx(draft)
        selected = set(request.entry_ids)
        entries = [
            entry
            for material in draft.materials
            for entry in material.entries
            if entry.id in selected
        ]
        codes = {(entry.code, entry.status_code, entry.unit) for entry in entries}
        if (
            len(entries) != len(request.entry_ids)
            or len(codes) != 1
            or any(entry.state != "code_review" for entry in entries)
            or request.review.delivery_date is not None
        ):
            raise HTTPException(422, detail="invalid_delivery_review_group")
        return _save_reviews(connection, draft, entries, "code_review", request.review, actor)


def _save_reviews(
    connection: Connection,
    draft: DeliveryDraft,
    entries: list[DraftEntry],
    kind: EntryReviewKind,
    values: EntryReviewInput,
    actor: User,
) -> DeliveryDraft:
    if not all(valid_entry_review(entry, kind, values) for entry in entries):
        raise HTTPException(422, detail="invalid_delivery_review")
    review = EntryReview(
        **values.model_dump(), kind=kind, reviewed_by=actor.username, reviewed_at=datetime.now(UTC)
    )
    for entry in entries:
        apply_entry_review(entry, review)
    _recount(draft)
    return _replace_draft(connection, draft.id, draft, actor.id)


@router.get("/templates")
def list_templates(_: SignedInUser) -> list[WorkbookTemplate]:
    return list(WORKBOOK_TEMPLATES)


@router.get("/templates/drx/rules")
def list_drx_rules(
    actor: SignedInUser, sample_id: Annotated[int, Query(gt=0)] | None = None
) -> DrxRules:
    with get_engine().connect() as connection:
        samples = connection.execute(
            text("""
            SELECT att.id AS attachment_id, att.filename, run.parser_version,
                   run.source_fields->'DelforNo'->>'value' AS document_no,
                   run.source_fields->'DelforCreateDate'->>'value' AS document_date
            FROM parse_runs AS run
            JOIN parse_jobs AS job ON job.id = run.job_id
            JOIN received_attachments AS att ON att.id = job.attachment_id
            WHERE run.template = 'drx_delfor'
            ORDER BY (att.id = :sample_id) DESC NULLS LAST, run.id DESC LIMIT 100
        """),
            {"sample_id": sample_id},
        ).mappings()
        return DrxRules(
            active=active_rule(connection),
            versions=rule_versions(connection, published_only=actor.role != "admin"),
            samples=[RuleSample.model_validate(row) for row in samples],
        )


def _delivery_source(
    connection: Connection, *, attachment_id: int, drx_only: bool = False
) -> RowMapping:
    templates = ["drx_delfor"] if drx_only else list(DELIVERY_CUSTOMERS)
    source = (
        connection.execute(
            text(_ATTACHMENT + " WHERE run.template = ANY(:templates) AND att.id = :id"),
            {"id": attachment_id, "templates": templates},
        )
        .mappings()
        .one_or_none()
    )
    if source is None:
        raise HTTPException(404, detail="delivery_source_unavailable")
    return source


def _rule_impact(
    draft: DraftSnapshot,
    source: RowMapping,
    baseline: DeliveryLeadTimes,
    candidate: DeliveryLeadTimes,
    simulated_date: date | None = None,
) -> RuleImpact:
    try:
        return compare_lead_times(
            draft,
            baseline,
            candidate,
            parser_version=source["parser_version"],
            simulated_date=simulated_date,
            sample_revision=draft.revision if isinstance(draft, DeliveryDraft) else None,
        )
    except OverflowError:
        raise HTTPException(422, detail="delivery_eta_out_of_range") from None


@router.post("/templates/drx/rules/preview", dependencies=_BROWSER, status_code=201)
def preview_drx_rule(request: TryRule, actor: AdminUser) -> DrxRuleVersion:
    with get_engine().begin() as connection:
        baseline = active_rule(connection)
        if baseline.id != request.baseline_id:
            raise HTTPException(409, detail="rule_baseline_changed")
        if baseline.parameters == request.parameters:
            raise HTTPException(422, detail="rule_parameters_unchanged")
        source = _delivery_source(connection, attachment_id=request.attachment_id, drx_only=True)
        existing = (
            connection.execute(
                text("SELECT * FROM delivery_drafts WHERE run_id = :id AND deleted_at IS NULL"),
                {"id": source["id"]},
            )
            .mappings()
            .one_or_none()
        )
        sample = (
            _snapshot(existing)
            if existing
            else _build_drx_snapshot(connection, source, rule=baseline)
        )
        impact = _rule_impact(
            sample, source, baseline.parameters, request.parameters, request.simulated_date
        )
        version_id = connection.execute(
            text("""
            INSERT INTO drx_rule_versions
                (air_days, sea_days, source_note, baseline_id, trial, created_by)
            VALUES (:air_days, :sea_days, :note, :baseline, :trial, :actor) RETURNING id
        """),
            {
                **request.parameters.model_dump(),
                "note": request.source_note,
                "baseline": baseline.id,
                "trial": Jsonb(impact.model_dump(mode="json")),
                "actor": actor.id,
            },
        ).scalar_one()
        version = rule_by_id(connection, version_id)
        assert version is not None
        return version


@router.post("/templates/drx/rules/{version_id}/publish", dependencies=_BROWSER)
def publish_drx_rule(version_id: int, actor: AdminUser) -> DrxRuleVersion:
    with get_engine().begin() as connection:
        connection.execute(text("SELECT id FROM drx_rule_versions WHERE id = 2 FOR UPDATE"))
        candidate = rule_by_id(connection, version_id)
        if candidate is None:
            raise HTTPException(404, detail="rule_version_not_found")
        if candidate.published_at is not None:
            return candidate
        if candidate.baseline_id != active_rule(connection).id:
            raise HTTPException(409, detail="rule_baseline_changed")
        assert candidate.trial is not None
        source = _delivery_source(
            connection, attachment_id=candidate.trial.attachment_id, drx_only=True
        )
        current_draft_revision = connection.scalar(
            text(
                "SELECT revision FROM delivery_drafts "
                "WHERE run_id = :id AND deleted_at IS NULL FOR SHARE"
            ),
            {"id": source["id"]},
        )
        if current_draft_revision != candidate.trial.sample_revision:
            raise HTTPException(409, detail="rule_sample_changed")
        connection.execute(
            text("""
            UPDATE drx_rule_versions SET published_at = clock_timestamp(), published_by = :actor
            WHERE id = :id
        """),
            {"id": version_id, "actor": actor.id},
        )
        published = rule_by_id(connection, version_id)
        assert published is not None
        return published


@router.get("/delivery/{run_id}/rule-preview")
def preview_draft_rule(run_id: int, _: AdminUser) -> DraftRulePreview | None:
    with get_engine().connect() as connection:
        draft = _snapshot(_draft_row(connection, run_id))
        _require_drx(draft)
        target = active_rule(connection)
        if target.version == draft.rule_version:
            return None
        source = _delivery_source(connection, attachment_id=draft.attachment_id, drx_only=True)
        impact = _rule_impact(draft, source, draft.rule_parameters, target.parameters)
        return DraftRulePreview(target=target, impact=impact, revision=draft.revision)


@router.post("/delivery/{run_id}/apply-rule", dependencies=_BROWSER)
def apply_draft_rule(run_id: int, request: ApplyRule, actor: AdminUser) -> DeliveryDraft:
    with get_engine().begin() as connection:
        # Publish and adopt use the same lock ordering so the preview target stays active.
        connection.execute(text("SELECT id FROM drx_rule_versions WHERE id = 2 FOR SHARE"))
        row = _draft_row(connection, run_id, lock=True)
        draft = _snapshot(row)
        _check_revision(draft, request.revision)
        _require_drx(draft)
        target = active_rule(connection)
        if target.id != request.target_id:
            raise HTTPException(409, detail="rule_baseline_changed")
        if target.version == draft.rule_version:
            return draft
        for material in draft.materials:
            for entry in material.entries:
                if entry.shipment:
                    entry.eta = _shipment_eta(entry.shipment, target.parameters)
        connection.execute(
            text("""
            INSERT INTO delivery_draft_rule_history (run_id, revision, snapshot, replaced_by)
            VALUES (:id, :revision, :snapshot, :actor)
        """),
            {
                "id": run_id,
                "revision": draft.revision,
                "snapshot": Jsonb(row["snapshot"]),
                "actor": actor.id,
            },
        )
        draft.rule_version = target.version
        draft.rule_parameters = target.parameters
        return _replace_draft(connection, run_id, draft, actor.id)


@router.get("/delivery/{run_id}/rule-history")
def list_draft_rule_history(run_id: int, _: SignedInUser) -> list[DraftRuleHistory]:
    with get_engine().connect() as connection:
        _draft_row(connection, run_id)
        rows = connection.execute(
            text("""
            SELECT revision, snapshot->>'rule_version' AS rule_version, replaced_at
            FROM delivery_draft_rule_history WHERE run_id = :id ORDER BY revision DESC
        """),
            {"id": run_id},
        ).mappings()
        return [DraftRuleHistory.model_validate(row) for row in rows]


def _workbook_response(content: bytes, filename: str) -> Response:
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
                if filename.isascii()
                else f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/delivery/{run_id}/draft.xlsx")
def download_draft(run_id: int, _: SignedInUser, revision: Annotated[int, Query(ge=1)]) -> Response:
    with get_engine().connect() as connection:
        draft = _snapshot(_draft_row(connection, run_id))
    _check_revision(draft, revision)
    label = "DRX" if draft.template == "drx_delfor" else draft.template
    return _workbook_response(delivery_workbook(draft), f"Relay-{label}-{run_id}-draft.xlsx")


@router.get("/delivery/{run_id}/rule-history/{revision}/draft.xlsx")
def download_draft_rule_history(run_id: int, revision: int, _: SignedInUser) -> Response:
    with get_engine().connect() as connection:
        _draft_row(connection, run_id)
        snapshot = connection.scalar(
            text("""
            SELECT snapshot FROM delivery_draft_rule_history
            WHERE run_id = :id AND revision = :revision
        """),
            {"id": run_id, "revision": revision},
        )
    if snapshot is None:
        raise HTTPException(404, detail="draft_not_found")
    return _workbook_response(
        delivery_workbook(DraftSnapshot.model_validate(snapshot)),
        f"Relay-DRX-{run_id}-v{revision}.xlsx",
    )


@router.get("/delivery/{run_id}/releases")
def read_releases(run_id: int, _: SignedInUser) -> list[DeliveryRelease]:
    with get_engine().connect() as connection:
        _draft_row(connection, run_id)
        return list_releases(connection, run_id)


@router.get("/delivery/{run_id}/release-preview")
def preview_release(
    run_id: int,
    revision: Annotated[int, Query(ge=1)],
    _: AdminUser,
    ship_through: date | None = None,
) -> ReleasePreview:
    with get_engine().connect() as connection:
        draft = _snapshot(_draft_row(connection, run_id))
        _check_revision(draft, revision)
        return _check_confirmation(draft, ship_through)


def _check_confirmation(draft: DeliveryDraft, ship_through: date | None) -> ReleasePreview:
    if draft.template == "drx_delfor" and ship_through is None:
        raise HTTPException(422, detail="release_cutoff_required")
    if draft.template != "drx_delfor" and ship_through is not None:
        raise HTTPException(422, detail="release_unexpected_cutoff")
    return check_release(draft, ship_through)


@router.post("/delivery/{run_id}/releases", dependencies=_BROWSER, status_code=201)
def confirm_release(run_id: int, request: ConfirmRelease, actor: AdminUser) -> DeliveryRelease:
    with get_engine().begin() as connection:
        draft = _snapshot(_draft_row(connection, run_id, lock=True))
        _check_revision(draft, request.revision)
        releases = list_releases(connection, run_id)
        latest = releases[0] if releases else None
        if latest and latest.draft_revision == request.revision:
            if latest.ship_through != request.ship_through:
                raise HTTPException(409, detail="release_already_confirmed")
            return latest
        preview = _check_confirmation(draft, request.ship_through)
        if preview.blocked:
            raise HTTPException(409, detail="release_has_blockers")
        if preview.retained_issues and not request.acknowledge_retained:
            raise HTTPException(409, detail="release_retained_ack_required")
        if latest and connection.scalar(
            text("""
                SELECT 1 FROM delivery_dispatch_recipients WHERE release_id = :id
                AND state IN ('queued', 'sending') LIMIT 1
            """),
            {"id": latest.id},
        ):
            raise HTTPException(409, detail="release_dispatch_in_progress")
        try:
            return freeze_release(
                connection,
                draft,
                run_id=run_id,
                revision=request.revision,
                version=latest.version + 1 if latest else 1,
                ship_through=request.ship_through,
                retained_issues=preview.retained_issues,
                actor_id=actor.id,
            )
        except ValueError as error:
            if str(error) != "release_workbook_too_large":
                raise
            raise HTTPException(422, detail="release_workbook_too_large") from None


@router.get("/delivery/{run_id}/releases/{version}/workbook.xlsx")
def download_release(run_id: int, version: int, _: SignedInUser) -> Response:
    with get_engine().connect() as connection:
        row = connection.execute(
            text("""
                SELECT filename, workbook, checksum FROM delivery_releases
                WHERE run_id = :run_id AND version = :version
            """),
            {"run_id": run_id, "version": version},
        ).one_or_none()
    if row is None:
        raise HTTPException(404, detail="release_not_found")
    workbook = bytes(row.workbook)
    if sha256(workbook).hexdigest() != row.checksum:
        raise HTTPException(500, detail="release_workbook_damaged")
    return _workbook_response(workbook, row.filename)
