import argparse
import imaplib
import logging
import time
from functools import partial
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy.exc import SQLAlchemyError

from relay.mail.configuration import (
    get_mail_settings,
    is_sync_enabled,
    load_mailbox_credentials,
    record_sync_result,
)
from relay.mail.sync import SYNC_BATCH_SIZE, SyncResetRequired, sync_once
from relay.storage.archive import archive_from_environment

_LOG = logging.getLogger(__name__)
_SETTINGS_INTERVAL = 5


def main() -> None:
    arguments = argparse.ArgumentParser(description="Synchronize one enabled 263 folder")
    arguments.add_argument("--once", action="store_true", help="Synchronize at most one batch")
    flags = arguments.parse_args()
    logging.basicConfig(level=logging.INFO)
    archive = archive_from_environment()
    previous_revision: UUID | None = None
    next_poll = 0.0
    while True:
        revision: UUID | None = None
        delay = 60
        try:
            if not is_sync_enabled():
                previous_revision = None
                if flags.once:
                    _LOG.info("No enabled mailbox")
                    return
                time.sleep(_SETTINGS_INTERVAL)
                continue
            mailbox = load_mailbox_credentials(get_mail_settings().cipher(), enabled_only=True)
            if mailbox is None or mailbox.revision is None:
                if flags.once:
                    return
                time.sleep(_SETTINGS_INTERVAL)
                continue
            revision = mailbox.revision
            if not flags.once and previous_revision == revision and time.monotonic() < next_poll:
                time.sleep(_SETTINGS_INTERVAL)
                continue
            delay = mailbox.poll_minutes * 60
            processed = sync_once(
                mailbox,
                archive,
                keep_running=partial(is_sync_enabled, revision),
            )
            record_sync_result(revision, "ok", processed)
            if processed:
                _LOG.info("Mail intake processed %s messages", processed)
            if processed >= SYNC_BATCH_SIZE:
                delay = 1
        except SyncResetRequired:
            if revision is not None:
                record_sync_result(revision, "uidvalidity_changed", None)
            _LOG.warning("Mailbox UIDVALIDITY changed; cursor review required")
            if flags.once:
                raise SystemExit(1) from None
        except (InvalidToken, ValueError, OSError, imaplib.IMAP4.error, SQLAlchemyError):
            if revision is not None:
                try:
                    record_sync_result(revision, "sync_failed", None)
                except SQLAlchemyError:
                    pass
            _LOG.warning("Mail intake failed; cursor retains the last durable UID")
            if flags.once:
                raise SystemExit(1) from None
        if flags.once:
            return
        previous_revision = revision
        next_poll = time.monotonic() + delay
        time.sleep(min(delay, _SETTINGS_INTERVAL))


if __name__ == "__main__":
    main()
