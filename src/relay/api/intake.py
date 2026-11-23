from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text

from relay.api.auth import SignedInUser
from relay.database.session import get_engine
from relay.storage.archive import archive_from_environment

router = APIRouter(prefix="/api/v1/intake", tags=["intake"])


class MailSummary(BaseModel):
    id: int
    sender: str
    subject: str
    received_at: datetime
    intake_error_code: str | None
    attachment_count: int


class AttachmentSummary(BaseModel):
    id: int
    filename: str
    size_bytes: int | None
    intake_state: str
    job_state: str | None
    error_code: str | None
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
    raw_interval_start: str | None
    raw_interval_end: str | None


_MAIL_SUMMARY = """
    SELECT mail.id, mail.sender, mail.subject, mail.received_at, mail.intake_error_code,
    (SELECT count(*) FROM received_attachments WHERE mail_id = mail.id) AS attachment_count
    FROM received_mail AS mail
"""


@router.get("/emails")
def emails(
    _: SignedInUser,
    before: Annotated[int, Query(ge=1)] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MailSummary]:
    query = _MAIL_SUMMARY
    if before is not None:
        query += " WHERE mail.id < :before"
    query += " ORDER BY mail.id DESC LIMIT :limit"
    with get_engine().connect() as connection:
        rows = connection.execute(text(query), {"before": before, "limit": limit}).mappings().all()
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
                    "job.state AS job_state, job.error_code, run.template "
                    "FROM received_attachments AS att "
                    "LEFT JOIN archive_objects AS blob ON blob.sha256 = att.sha256 "
                    "LEFT JOIN parse_jobs AS job ON job.attachment_id = att.id "
                    "LEFT JOIN parse_runs AS run ON run.job_id = job.id "
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
