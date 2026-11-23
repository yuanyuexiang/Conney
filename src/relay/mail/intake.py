from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from sqlalchemy import text

from relay.database.session import get_engine
from relay.storage.archive import MAX_ATTACHMENT_BYTES, Archive, ArchivedObject

MAX_MESSAGE_BYTES = 32 * 1024 * 1024
_MAX_MIME_DEPTH = 30
_MAX_MIME_PARTS = 1000


@dataclass(frozen=True)
class MailIdentity:
    source: str
    folder: str
    uidvalidity: int
    uid: int
    received_at: datetime


@dataclass(frozen=True)
class Attachment:
    part: str
    filename: str
    content_type: str
    state: str
    archived: ArchivedObject | None


@dataclass(frozen=True)
class IntakeResult:
    mail_id: int
    created: bool


def _parts(message: EmailMessage) -> list[tuple[str, EmailMessage]]:
    found: list[tuple[str, EmailMessage]] = []
    pending = [("1", message, 1)]
    visited = 0
    while pending:
        path, part, depth = pending.pop()
        visited += 1
        if visited > _MAX_MIME_PARTS or depth > _MAX_MIME_DEPTH or len(path) > 100:
            raise ValueError("Message MIME tree exceeds limits")
        if part.get_content_disposition() == "attachment" or part.get_filename() is not None:
            found.append((path, part))
        elif part.get_content_maintype() == "multipart":
            children = list(part.iter_parts())
            pending.extend(
                (f"{path}.{index}", child, depth + 1)
                for index, child in reversed(list(enumerate(children, start=1)))
            )
    return found


def _archive_attachment(part_id: str, message: EmailMessage, archive: Archive) -> Attachment:
    filename = (message.get_filename() or "attachment").replace("\x00", "")[:512]
    content_type = message.get_content_type()
    payload = message.get_payload(decode=True)
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_ATTACHMENT_BYTES:
        return Attachment(part_id, filename, content_type, "rejected", None)
    workbook = filename.lower().endswith(".xlsx") and payload.startswith(b"PK\x03\x04")
    if filename.lower().endswith(".xlsx") and not workbook:
        return Attachment(part_id, filename, content_type, "rejected", archive.store(payload))
    return Attachment(
        part_id,
        filename,
        content_type,
        "archived" if workbook else "unsupported",
        archive.store(payload),
    )


def receive(raw_message: bytes, identity: MailIdentity, archive: Archive) -> IntakeResult:
    if not raw_message or len(raw_message) > MAX_MESSAGE_BYTES:
        raise ValueError("Invalid message size")
    if min(identity.uid, identity.uidvalidity) < 1 or identity.received_at.utcoffset() is None:
        raise ValueError("Invalid mail identity")
    params = {
        "source": identity.source,
        "folder": identity.folder,
        "uidvalidity": identity.uidvalidity,
        "uid": identity.uid,
        "received_at": identity.received_at,
    }
    lookup = text(
        "SELECT id FROM received_mail WHERE source = :source AND folder = :folder "
        "AND uidvalidity = :uidvalidity AND uid = :uid"
    )
    with get_engine().connect() as connection:
        existing = connection.execute(lookup, params).scalar_one_or_none()
    if existing is not None:
        return IntakeResult(int(existing), False)

    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    attachments = [_archive_attachment(part, child, archive) for part, child in _parts(message)]
    fields = {
        **params,
        "subject": str(message.get("Subject", "")).replace("\x00", "")[:4096],
        "sender": str(message.get("From", "")).replace("\x00", "")[:2048],
        "message_id": str(message.get("Message-ID", "")).replace("\x00", "")[:2048] or None,
    }
    with get_engine().begin() as connection:
        mail_id = connection.execute(
            text(
                "INSERT INTO received_mail (source, folder, uidvalidity, uid, received_at, "
                "subject, sender, message_id) VALUES (:source, :folder, :uidvalidity, :uid, "
                ":received_at, :subject, :sender, :message_id) "
                "ON CONFLICT (source, folder, uidvalidity, uid) DO NOTHING RETURNING id"
            ),
            fields,
        ).scalar_one_or_none()
        if mail_id is None:
            return IntakeResult(int(connection.execute(lookup, params).scalar_one()), False)
        for attachment in attachments:
            recorded = attachment.archived
            if recorded is not None:
                connection.execute(
                    text(
                        "INSERT INTO archive_objects (sha256, size_bytes) VALUES (:sha256, :size) "
                        "ON CONFLICT (sha256) DO NOTHING"
                    ),
                    {"sha256": recorded.sha256, "size": recorded.size_bytes},
                )
            attachment_id = connection.execute(
                text(
                    "INSERT INTO received_attachments (mail_id, mime_part, filename, mime_type, "
                    "sha256, intake_state) VALUES (:mail_id, :part, :filename, :mime_type, "
                    ":sha256, :state) RETURNING id"
                ),
                {
                    "mail_id": mail_id,
                    "part": attachment.part,
                    "filename": attachment.filename,
                    "mime_type": attachment.content_type,
                    "sha256": recorded.sha256 if recorded else None,
                    "state": attachment.state,
                },
            ).scalar_one()
            if attachment.state == "archived":
                connection.execute(
                    text("INSERT INTO parse_jobs (attachment_id) VALUES (:id)"),
                    {"id": attachment_id},
                )
    return IntakeResult(int(mail_id), True)
