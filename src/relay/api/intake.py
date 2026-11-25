from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import Connection, text

from relay.api.auth import SignedInUser
from relay.database.session import get_engine
from relay.storage.archive import archive_from_environment

router = APIRouter(prefix="/api/v1/intake", tags=["intake"])

MailState = Literal["failed", "review", "queued", "complete", "archived"]
MailFilter = Literal["all", "actionable", "failed", "review", "queued", "complete", "archived"]


class MailSummary(BaseModel):
    id: int
    sender: str
    subject: str
    received_at: datetime
    intake_error_code: str | None
    attachment_count: int
    templates: list[str]
    state: MailState


class IntakeCounts(BaseModel):
    total: int
    failed: int
    review: int
    queued: int


class AttachmentSummary(BaseModel):
    id: int
    filename: str
    size_bytes: int | None
    intake_state: str
    job_state: str | None
    error_code: str | None
    error_location: str | None
    error_reason: str | None
    review_location: str | None
    template: str | None


class MailDetail(BaseModel):
    mail: MailSummary
    attachments: list[AttachmentSummary]


class RawQuantity(BaseModel):
    id: int
    material: str | None
    quantity: str
    quantity_type: str | None
    unit: str | None
    requested_date: date | None
    earliest_date: date | None
    latest_date: date | None
    quantity_cell: str | None
    raw_delivery_date: str | None
    delivery_date_cell: str | None
    raw_interval_start: str | None
    raw_interval_end: str | None


_REVIEW_LOCATION = """
    SELECT qty.source_fields->'DateTime'->>'cell' AS location
    FROM parsed_material_blocks AS block
    JOIN parsed_quantity_records AS qty ON qty.block_id = block.id
    WHERE run.template = 'kroschu_4905' AND block.run_id = run.id
    AND qty.source_fields->'DateTime'->>'value' = '000000'
    ORDER BY block.position, qty.position LIMIT 1
"""
_ATTACHMENT_ROWS = f"""
    FROM received_attachments AS att
    LEFT JOIN parse_jobs AS job ON job.attachment_id = att.id
    LEFT JOIN parse_runs AS run ON run.job_id = job.id
    LEFT JOIN LATERAL ({_REVIEW_LOCATION}) AS review ON true
"""
_MAIL_SUMMARY = f"""
    SELECT mail.id, mail.sender, mail.subject, mail.received_at, mail.intake_error_code,
    att_summary.attachment_count, att_summary.templates,
    CASE
        WHEN mail.intake_error_code IS NOT NULL OR att_summary.failed THEN 'failed'
        WHEN att_summary.for_review THEN 'review'
        WHEN att_summary.in_progress THEN 'queued'
        WHEN att_summary.succeeded THEN 'complete'
        ELSE 'archived'
    END AS state
    FROM received_mail AS mail
    CROSS JOIN LATERAL (
        SELECT count(*) AS attachment_count,
        COALESCE(array_agg(DISTINCT run.template ORDER BY run.template)
            FILTER (WHERE run.template IS NOT NULL), ARRAY[]::varchar[]) AS templates,
        bool_or(att.intake_state = 'rejected' OR job.state = 'failed') AS failed,
        bool_or(review.location IS NOT NULL) AS for_review,
        bool_or(job.state IN ('pending', 'processing')) AS in_progress,
        bool_or(job.state = 'succeeded') AS succeeded
        {_ATTACHMENT_ROWS}
        WHERE att.mail_id = mail.id
    ) AS att_summary
"""
_ACTIONABLE_STATES = "('failed', 'review', 'queued')"


def latest_mail(connection: Connection, limit: int) -> list[MailSummary]:
    rows = connection.execute(
        text(
            f"{_MAIL_SUMMARY} "
            "WHERE mail.id IN (SELECT id FROM received_mail "
            "ORDER BY received_at DESC, id DESC LIMIT :limit) "
            "ORDER BY mail.received_at DESC, mail.id DESC"
        ),
        {"limit": limit},
    ).mappings()
    return [MailSummary.model_validate(row) for row in rows]


@router.get("/counts")
def counts(_: SignedInUser) -> IntakeCounts:
    with get_engine().connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT count(*) AS total, count(*) FILTER (WHERE state = 'failed') AS failed, "
                    "count(*) FILTER (WHERE state = 'review') AS review, "
                    "count(*) FILTER (WHERE state = 'queued') AS queued "
                    f"FROM ({_MAIL_SUMMARY}) AS summary"
                )
            )
            .mappings()
            .one()
        )
    return IntakeCounts.model_validate(row)


@router.get("/emails")
def emails(
    _: SignedInUser,
    before: Annotated[int, Query(ge=1)] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    state: MailFilter = "all",
    q: Annotated[str, Query(max_length=120)] = "",
) -> list[MailSummary]:
    criteria = ["true"]
    filters = ["true"]
    if before is not None:
        criteria.append("mail.id < :before")
    if q.strip():
        criteria.append(
            "(strpos(lower(mail.sender), lower(:query)) > 0 "
            "OR strpos(lower(mail.subject), lower(:query)) > 0 "
            "OR EXISTS (SELECT 1 FROM received_attachments "
            "WHERE mail_id = mail.id AND strpos(lower(filename), lower(:query)) > 0))"
        )
    if state == "actionable":
        filters.append(f"summary.state IN {_ACTIONABLE_STATES}")
    elif state != "all":
        filters.append("summary.state = :state")
    query = text(
        f"SELECT * FROM ({_MAIL_SUMMARY} WHERE {' AND '.join(criteria)}) AS summary "
        f"WHERE {' AND '.join(filters)} ORDER BY id DESC LIMIT :limit"
    )
    with get_engine().connect() as connection:
        rows = (
            connection.execute(
                query, {"before": before, "limit": limit, "query": q.strip(), "state": state}
            )
            .mappings()
            .all()
        )
    return [MailSummary.model_validate(row) for row in rows]


@router.get("/emails/{mail_id}")
def email_detail(mail_id: int, _: SignedInUser) -> MailDetail:
    with get_engine().connect() as connection:
        mail = (
            connection.execute(text(_MAIL_SUMMARY + " WHERE mail.id = :id"), {"id": mail_id})
            .mappings()
            .one_or_none()
        )
        if mail is None:
            raise HTTPException(404, detail="mail_not_found")
        attachments = (
            connection.execute(
                text(
                    "SELECT att.id, att.filename, att.intake_state, blob.size_bytes, "
                    "job.state AS job_state, job.error_code, job.error_reason, job.error_location, "
                    "run.template, "
                    "review.location AS review_location "
                    f"{_ATTACHMENT_ROWS} "
                    "LEFT JOIN archive_objects AS blob ON blob.sha256 = att.sha256 "
                    "WHERE att.mail_id = :id ORDER BY att.id"
                ),
                {"id": mail_id},
            )
            .mappings()
            .all()
        )
    return MailDetail(
        mail=MailSummary.model_validate(mail),
        attachments=[AttachmentSummary.model_validate(row) for row in attachments],
    )


@router.get("/attachments/{attachment_id}/quantities")
def quantities(
    attachment_id: int,
    _: SignedInUser,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[RawQuantity]:
    with get_engine().connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT qty.id, COALESCE(block.source_fields->'BuyerItemNo'->>'value', "
                    "block.source_fields->'CustomerItemNo'->>'value') AS material, "
                    "CAST(qty.quantity AS text) AS quantity, qty.requested_date, "
                    "qty.earliest_date, "
                    "qty.latest_date, qty.source_fields->'QtyType'->>'value' AS quantity_type, "
                    "qty.source_fields->'QtyUnit'->>'value' AS unit, "
                    "qty.source_fields->'Qty'->>'cell' AS quantity_cell, "
                    "COALESCE(qty.source_fields->'RequestedDeliveryDate'->>'value', "
                    "qty.source_fields->'DateTime'->>'value') AS raw_delivery_date, "
                    "COALESCE(qty.source_fields->'RequestedDeliveryDate'->>'cell', "
                    "qty.source_fields->'DateTime'->>'cell') AS delivery_date_cell, "
                    "qty.source_fields->'HorizonStartDate'->>'value' AS raw_interval_start, "
                    "qty.source_fields->'HorizonEndDate'->>'value' AS raw_interval_end "
                    "FROM parsed_quantity_records AS qty "
                    "JOIN parsed_material_blocks AS block ON block.id = qty.block_id "
                    "JOIN parse_runs AS run ON run.id = block.run_id "
                    "JOIN parse_jobs AS job ON job.id = run.job_id "
                    "WHERE job.attachment_id = :id AND qty.id > :after "
                    "ORDER BY qty.id LIMIT :limit"
                ),
                {"id": attachment_id, "after": after, "limit": limit},
            )
            .mappings()
            .all()
        )
    return [RawQuantity.model_validate(row) for row in rows]


@router.get("/attachments/{attachment_id}/file")
def original_file(attachment_id: int, _: SignedInUser) -> FileResponse:
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT att.filename, blob.sha256, blob.size_bytes "
                "FROM received_attachments AS att "
                "JOIN archive_objects AS blob ON blob.sha256 = att.sha256 WHERE att.id = :id"
            ),
            {"id": attachment_id},
        ).one_or_none()
    if row is None:
        raise HTTPException(404, detail="attachment_not_archived")
    try:
        path = archive_from_environment().verified_path(row.sha256, row.size_bytes)
    except OSError:
        raise HTTPException(503, detail="archive_unavailable") from None
    filename = row.filename.replace("/", "_").replace("\\", "_")
    return FileResponse(
        path,
        filename=filename or "attachment",
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
