import argparse
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from xml.etree.ElementTree import ParseError as XmlParseError

from defusedxml.common import DefusedXmlException
from sqlalchemy.exc import SQLAlchemyError

from relay.jobs.parsing import ParseLease, claim, complete, fail, renew
from relay.parsers.document import ParsedDocument, ParseError
from relay.parsers.schedule_workbooks import parse_workbook
from relay.storage.archive import Archive, ArchiveIntegrityError, archive_from_environment
from relay.workers.heartbeat import WorkerHeartbeat

_LOG = logging.getLogger(__name__)


def _parse(archive: Archive, lease: ParseLease) -> ParsedDocument:
    return parse_workbook(archive.verified_path(lease.sha256, lease.size_bytes))


def _wait_for_parse(
    future: Future[ParsedDocument], lease: ParseLease, heartbeat: Callable[[], None] | None
) -> ParsedDocument | None:
    while True:
        try:
            return future.result(timeout=20)
        except TimeoutError:
            if future.done():
                raise
            if not renew(lease):
                return None
            if heartbeat is not None:
                heartbeat()


def process_one(archive: Archive, *, heartbeat: Callable[[], None] | None = None) -> bool:
    lease = claim()
    if lease is None:
        return False
    try:
        with ThreadPoolExecutor(max_workers=1) as thread:
            document = _wait_for_parse(thread.submit(_parse, archive, lease), lease, heartbeat)
        if document is not None:
            complete(lease, document)
    except ArchiveIntegrityError:
        fail(lease, "archive_integrity", retryable=True)
    except ParseError as error:
        fail(
            lease,
            "invalid_or_unsupported_workbook",
            retryable=False,
            location=error.location,
            reason=error.reason,
        )
    except (XmlParseError, DefusedXmlException, ValueError):
        fail(lease, "invalid_or_unsupported_workbook", retryable=False)
    except OSError:
        fail(lease, "archive_io", retryable=True)
    return True


def main() -> None:
    arguments = argparse.ArgumentParser(description="Process archived Relay attachments")
    arguments.add_argument(
        "--once", action="store_true", help="Process at most one queued attachment"
    )
    flags = arguments.parse_args()
    logging.basicConfig(level=logging.INFO)
    archive = archive_from_environment()
    if flags.once:
        process_one(archive)
        return
    heartbeat = WorkerHeartbeat("parser")
    while True:
        try:
            heartbeat()
            if process_one(archive, heartbeat=heartbeat):
                continue
        except SQLAlchemyError:
            _LOG.warning("Parser database access failed; will retry")
        time.sleep(2)


if __name__ == "__main__":
    main()
