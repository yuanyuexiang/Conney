import logging
from hashlib import sha256
from time import sleep
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import SQLAlchemyError

from relay.database.session import get_engine
from relay.mail.configuration import get_mail_settings
from relay.mail.outgoing import (
    OutgoingMessage,
    SendResult,
    decrypt_credentials,
    read_credentials,
    send_workbook,
)
from relay.workers.heartbeat import WorkerHeartbeat

_LOG = logging.getLogger(__name__)


def _claim() -> RowMapping | None:
    with get_engine().begin() as connection:
        connection.execute(
            text("""
            UPDATE delivery_dispatch_recipients
            SET state = 'uncertain', detail = 'smtp_worker_interrupted',
                finished_at = clock_timestamp(), lease_until = NULL
            WHERE state = 'sending' AND lease_until <= clock_timestamp()
        """)
        )
        return (
            connection.execute(
                text("""
            WITH next AS (
                SELECT id FROM delivery_dispatch_recipients WHERE state = 'queued'
                ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
            ), claimed AS (
                UPDATE delivery_dispatch_recipients AS rec
                SET state = 'sending', lease_until = clock_timestamp() + interval '5 minutes'
                FROM next WHERE rec.id = next.id RETURNING rec.*
            )
            SELECT rec.id, rec.address, rec.message_id, d.sender, d.mailbox_revision, d.subject,
                d.body, r.filename, r.workbook, r.checksum
            FROM claimed AS rec JOIN delivery_dispatches AS d ON d.id = rec.dispatch_id
            JOIN delivery_releases AS r ON r.id = rec.release_id
        """)
            )
            .mappings()
            .one_or_none()
        )


def _send(row: RowMapping) -> SendResult:
    revision: UUID = row.mailbox_revision
    stored = read_credentials()
    if (
        stored is None
        or stored.revision != revision
        or not stored.verified
        or stored.address != row.sender
    ):
        return SendResult("failed", "outgoing_changed")
    try:
        config = decrypt_credentials(stored, get_mail_settings().cipher())
    except (ValueError, InvalidToken):
        return SendResult("failed", "mailbox_credential_invalid")
    if not config.allows(row.address):
        return SendResult("failed", "dispatch_address_not_allowed")
    workbook = bytes(row.workbook)
    if sha256(workbook).hexdigest() != row.checksum:
        return SendResult("failed", "release_workbook_damaged")
    message = OutgoingMessage(
        address=row.address,
        subject=row.subject,
        body=row.body,
        filename=row.filename,
        workbook=workbook,
        message_id=row.message_id,
    )
    return send_workbook(config, message)


def process_one() -> bool:
    row = _claim()
    if row is None:
        return False
    try:
        result = _send(row)
    except SQLAlchemyError:
        # Credential lookup failed before creating an SMTP connection.
        result = SendResult("failed", "dispatch_storage_unavailable")
    with get_engine().begin() as connection:
        connection.execute(
            text("""
            UPDATE delivery_dispatch_recipients
            SET state = :state, detail = :detail, smtp_code = :code,
                lease_until = NULL, finished_at = clock_timestamp()
            WHERE id = :id AND state = 'sending'
        """),
            {"id": row.id, "state": result.state, "detail": result.detail, "code": result.code},
        )
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    heartbeat = WorkerHeartbeat("delivery")
    while True:
        heartbeat()
        try:
            if process_one():
                continue
        except Exception:
            # Do not log email addresses, attachment contents, or the SMTP server reply.
            _LOG.warning("Delivery dispatch worker could not complete a job")
        sleep(3)


if __name__ == "__main__":
    main()
