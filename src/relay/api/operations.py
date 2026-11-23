import os
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from relay.api.auth import SignedInUser
from relay.database.session import get_engine
from relay.mail.configuration import MailboxSyncStatus, load_configuration
from relay.storage.archive import ArchiveIntegrityError, archive_from_environment
from relay.workers.heartbeat import HEARTBEAT_STALE_SECONDS

router = APIRouter(prefix="/api/v1/health", tags=["operations"])


class WorkerStatus(BaseModel):
    state: Literal["reporting", "stale", "unseen"]
    last_seen_at: datetime | None = None


class ParsingStatus(BaseModel):
    pending: int
    processing: int
    expired: int
    failed: int
    succeeded: int
    last_success_at: datetime | None


class MailboxOperations(MailboxSyncStatus):
    poll_minutes: int


class ArchiveStatus(BaseModel):
    state: Literal["empty", "verified", "unavailable", "damaged"]


class OperationsStatus(BaseModel):
    checked_at: datetime
    mail_worker: WorkerStatus
    parser_worker: WorkerStatus
    mailbox: MailboxOperations | None
    parsing: ParsingStatus
    archive: ArchiveStatus


def _worker(seen_at: datetime | None, checked_at: datetime) -> WorkerStatus:
    if seen_at is None:
        return WorkerStatus(state="unseen")
    age = (checked_at - seen_at).total_seconds()
    return WorkerStatus(
        state="stale" if age > HEARTBEAT_STALE_SECONDS else "reporting", last_seen_at=seen_at
    )


def _archive(digest: str | None, size_bytes: int | None) -> ArchiveStatus:
    archive = archive_from_environment()
    try:
        with os.scandir(archive.root) as entries:
            next(entries, None)
        if digest is None or size_bytes is None:
            return ArchiveStatus(state="empty")
        archive.verified_path(digest, size_bytes)
    except ArchiveIntegrityError:
        return ArchiveStatus(state="damaged")
    except OSError:
        return ArchiveStatus(state="unavailable")
    return ArchiveStatus(state="verified")


@router.get("/operations")
def read_operations(_: SignedInUser) -> OperationsStatus:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '4s'"))
            checked_at = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            workers = {
                row.worker: row.seen_at
                for row in connection.execute(text("SELECT worker, seen_at FROM worker_heartbeats"))
            }
            jobs = (
                connection.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE state = 'pending') AS pending, "
                        "count(*) FILTER (WHERE state = 'processing' AND lease_until > :at) "
                        "AS processing, "
                        "count(*) FILTER (WHERE state = 'processing' AND lease_until <= :at) "
                        "AS expired, "
                        "count(*) FILTER (WHERE state = 'failed') AS failed, "
                        "count(*) FILTER (WHERE state = 'succeeded') AS succeeded, "
                        "(SELECT max(created_at) FROM parse_runs) "
                        "AS last_success_at FROM parse_jobs"
                    ),
                    {"at": checked_at},
                )
                .mappings()
                .one()
            )
            latest = connection.execute(
                text(
                    "SELECT blob.sha256, blob.size_bytes FROM received_attachments AS att "
                    "JOIN archive_objects AS blob ON blob.sha256 = att.sha256 "
                    "ORDER BY att.id DESC LIMIT 1"
                )
            ).one_or_none()
        configuration = load_configuration()
    except SQLAlchemyError:
        raise HTTPException(503, detail="operations_unavailable") from None

    return OperationsStatus(
        checked_at=checked_at,
        mail_worker=_worker(workers.get("mail"), checked_at),
        parser_worker=_worker(workers.get("parser"), checked_at),
        mailbox=(
            None
            if configuration is None
            else MailboxOperations.model_validate(configuration.model_dump())
        ),
        parsing=ParsingStatus.model_validate(jobs),
        archive=_archive(latest.sha256, latest.size_bytes) if latest else _archive(None, None),
    )
