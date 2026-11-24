import imaplib
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text

from relay.database.session import get_engine
from relay.mail.connection import _encode_folder, identify_client
from relay.mail.intake import MAX_MESSAGE_BYTES, MailIdentity, receive
from relay.storage.archive import Archive

SYNC_BATCH_SIZE = 25
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailProtocolError(OSError):
    """The server did not supply the data required to checkpoint a message."""


class SyncResetRequired(MailProtocolError):
    """The server changed UIDVALIDITY and the old cursor requires review."""


@dataclass(frozen=True)
class SyncMailbox:
    address: str
    host: str
    folder: str
    since: date
    poll_minutes: int
    password: str = field(repr=False)
    revision: UUID | None = None

    @property
    def source(self) -> str:
        return f"imap:{self.host}:{self.address}"


def _load_cursor(mailbox: SyncMailbox, uidvalidity: int) -> int:
    key = {"source": mailbox.source, "folder": mailbox.folder, "epoch": uidvalidity}
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mail_sync_cursors (source, folder, uidvalidity) "
                "VALUES (:source, :folder, :epoch) ON CONFLICT (source, folder) DO NOTHING"
            ),
            key,
        )
        row = connection.execute(
            text(
                "SELECT uidvalidity, last_uid FROM mail_sync_cursors "
                "WHERE source = :source AND folder = :folder"
            ),
            key,
        ).one()
    if row.uidvalidity != uidvalidity:
        raise SyncResetRequired("Mailbox UIDVALIDITY changed")
    return int(row.last_uid)


def _advance_cursor(identity: MailIdentity) -> None:
    with get_engine().begin() as connection:
        row = connection.execute(
            text(
                "UPDATE mail_sync_cursors SET last_uid = GREATEST(last_uid, :uid), "
                "updated_at = now() "
                "WHERE source = :source AND folder = :folder AND uidvalidity = :epoch "
                "RETURNING last_uid"
            ),
            {
                "uid": identity.uid,
                "source": identity.source,
                "folder": identity.folder,
                "epoch": identity.uidvalidity,
            },
        ).one_or_none()
    if row is None:
        raise SyncResetRequired("Mailbox cursor changed during synchronization")


def _reject_message(identity: MailIdentity, error_code: str) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO received_mail (source, folder, uidvalidity, uid, received_at, "
                "subject, sender, intake_error_code) VALUES (:source, :folder, :epoch, "
                ":uid, :received_at, '', '', :error) "
                "ON CONFLICT (source, folder, uidvalidity, uid) DO NOTHING"
            ),
            {
                "source": identity.source,
                "folder": identity.folder,
                "epoch": identity.uidvalidity,
                "uid": identity.uid,
                "received_at": identity.received_at,
                "error": error_code,
            },
        )


def _header_metadata(client: imaplib.IMAP4, uid: int) -> tuple[int, datetime]:
    status, parts = client.uid("FETCH", str(uid), "(UID RFC822.SIZE INTERNALDATE)")
    if status == "OK":
        for entry in parts:
            if not isinstance(entry, bytes):
                continue
            uid_field = re.search(rb"\bUID (\d+)", entry)
            size_field = re.search(rb"\bRFC822\.SIZE (\d+)", entry)
            date_field = re.search(rb'\bINTERNALDATE "([^"]+)"', entry)
            if uid_field and size_field and date_field and int(uid_field[1]) == uid:
                try:
                    received_at = datetime.strptime(
                        date_field[1].decode("ascii").strip(), "%d-%b-%Y %H:%M:%S %z"
                    )
                except ValueError as error:
                    raise MailProtocolError("Invalid IMAP message date") from error
                return int(size_field[1]), received_at
    raise MailProtocolError("Missing IMAP message metadata")


def _fetch_message(client: imaplib.IMAP4, uid: int, size: int) -> bytes:
    status, parts = client.uid("FETCH", str(uid), f"(UID BODY.PEEK[]<0.{MAX_MESSAGE_BYTES + 1}>)")
    if status == "OK":
        for index, entry in enumerate(parts):
            if not isinstance(entry, tuple) or len(entry) != 2:
                continue
            header, payload = entry
            if not isinstance(header, bytes) or not isinstance(payload, bytes):
                continue
            uid_field = re.search(rb"\bUID (\d+)", header)
            # Some servers place UID after the literal, in the closing response line.
            if uid_field is None and index + 1 < len(parts):
                trailing = parts[index + 1]
                if isinstance(trailing, bytes):
                    uid_field = re.match(rb"\s+UID (\d+)", trailing)
            if uid_field and int(uid_field[1]) == uid and len(payload) == size:
                return payload
    raise MailProtocolError("Incomplete IMAP message")


def _sync_selected(
    client: imaplib.IMAP4,
    mailbox: SyncMailbox,
    archive: Archive,
    keep_running: Callable[[], bool] | None,
) -> int:
    status, _ = client.select(_encode_folder(mailbox.folder), readonly=True)
    if status != "OK":
        raise MailProtocolError("Cannot read configured mailbox folder")
    _, epochs = client.response("UIDVALIDITY")
    if not epochs or not isinstance(epochs[0], bytes) or not epochs[0].isdigit():
        raise MailProtocolError("Missing IMAP UIDVALIDITY")
    epoch = int(epochs[0])
    if epoch < 1:
        raise MailProtocolError("Invalid IMAP UIDVALIDITY")
    last_uid = _load_cursor(mailbox, epoch)
    if last_uid >= 4_294_967_295:
        return 0
    since = mailbox.since
    search_date = f"{since.day:02d}-{_MONTHS[since.month - 1]}-{since.year:04d}"
    status, results = client.uid("SEARCH", "UID", f"{last_uid + 1}:*", "SINCE", search_date)
    if status != "OK" or not results or not isinstance(results[0], bytes):
        raise MailProtocolError("IMAP UID search failed")
    tokens = results[0].split()
    if any(not item.isdigit() for item in tokens):
        raise MailProtocolError("IMAP UID search returned invalid identifiers")
    # Some servers return the prior highest UID for a range with '*'.
    candidates = sorted({int(item) for item in tokens if last_uid < int(item) <= 4_294_967_295})
    processed = 0
    for uid in candidates[:SYNC_BATCH_SIZE]:
        if keep_running is not None and not keep_running():
            break
        size, received_at = _header_metadata(client, uid)
        identity = MailIdentity(mailbox.source, mailbox.folder, epoch, uid, received_at)
        if size < 1 or size > MAX_MESSAGE_BYTES:
            _reject_message(identity, "message_size")
        else:
            raw = _fetch_message(client, uid, size)
            try:
                receive(raw, identity, archive)
            except (ValueError, RecursionError):
                _reject_message(identity, "invalid_message_content")
        _advance_cursor(identity)
        processed += 1
    return processed


def sync_once(
    mailbox: SyncMailbox, archive: Archive, *, keep_running: Callable[[], bool] | None = None
) -> int:
    with imaplib.IMAP4_SSL(
        mailbox.host, 993, ssl_context=ssl.create_default_context(), timeout=10
    ) as client:
        client.login(mailbox.address, mailbox.password)
        if not identify_client(client, mailbox.host):
            raise MailProtocolError("IMAP client identification failed")
        return _sync_selected(client, mailbox, archive, keep_running)
