import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from relay.api.app import app
from relay.auth.accounts import Role, User
from relay.database.session import get_engine
from relay.jobs.parsing import claim, complete, renew
from relay.mail import sync
from relay.mail.intake import MailIdentity, receive
from relay.mail.sync import SyncMailbox, SyncResetRequired, sync_once
from relay.parsers.vitesco import parse_vitesco
from relay.storage.archive import Archive
from relay.workers.parser import process_one


@pytest.fixture
def eml_and_workbook(tmp_path: Path) -> tuple[bytes, Path]:
    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Vitesco-DELFOR-长期交付预测"])
    sheet.append(["DelforNo", "001"])
    sheet.append(["LineItemNo", "1", "BuyerItemNo", "000123"])
    sheet.append(
        [
            "DeliveryPlanStatusIndicator",
            "QtyType",
            "Qty",
            "QtyUnit",
            "RequestedDeliveryDate",
            "EarliestDeliveryDate",
            "LastDeliveryDate",
        ]
    )
    sheet.append(["F", "113", "0.00", "C62", None, "20261214", "20261220"])
    workbook.save(path)
    mail = EmailMessage()
    mail["From"] = "demo@example.com"
    mail["Subject"] = "Delivery forecast"
    mail["Message-ID"] = "<demo@example.com>"
    mail.set_content("Attached forecast")
    mail.add_attachment(
        path.read_bytes(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="forecast.xlsx",
    )
    mail.add_attachment(b"test", maintype="text", subtype="plain", filename="readme.txt")
    return mail.as_bytes(), path


def _identity(uid: int) -> MailIdentity:
    return MailIdentity("local:test-eml", "INBOX", 41, uid, datetime.now(UTC))


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_replay_recovers_transaction_and_deduplicates_archive(
    tmp_path: Path,
    eml_and_workbook: tuple[bytes, Path],
    monkeypatch: pytest.MonkeyPatch,
    signed_in: Callable[[TestClient, Role], User],
) -> None:
    message, workbook = eml_and_workbook
    archive = Archive(tmp_path / "archive")
    engine = get_engine()

    def fail_on_insert(*args: object) -> None:
        if str(args[2]).startswith("INSERT INTO received_mail"):
            raise SQLAlchemyError("intake transaction failed")

    event.listen(engine, "before_cursor_execute", fail_on_insert)
    try:
        with pytest.raises(SQLAlchemyError, match="intake transaction failed"):
            receive(message, _identity(1), archive)
        assert sorted(path.read_bytes() for path in archive.root.iterdir()) == sorted(
            [workbook.read_bytes(), b"test"]
        )
        with engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM received_mail")) == 0
            assert db.scalar(text("SELECT count(*) FROM parse_jobs")) == 0
    finally:
        event.remove(engine, "before_cursor_execute", fail_on_insert)

    first = receive(message, _identity(1), archive)
    duplicate = receive(message, _identity(1), archive)
    second = receive(message, _identity(2), archive)
    assert first.created and not duplicate.created and second.created
    assert first.mail_id == duplicate.mail_id != second.mail_id

    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM archive_objects")) == 2
        assert db.scalar(text("SELECT count(*) FROM received_attachments")) == 4
        assert db.scalar(text("SELECT count(*) FROM parse_jobs")) == 2

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(lambda _: claim(), range(2)))
    old, other = leases
    assert old is not None and other is not None and old.id != other.id
    assert renew(other)
    with engine.begin() as db:
        db.execute(
            text("UPDATE parse_jobs SET lease_until = now() - interval '1 second' WHERE id = :id"),
            {"id": old.id},
        )
    document = parse_vitesco(workbook)
    assert not renew(old) and not complete(old, document)

    # A lost original retries without publishing partial output. Restoring bytes allows recovery.
    archive.verified_path(old.sha256, old.size_bytes).unlink()
    assert process_one(archive)
    with engine.begin() as db:
        assert db.execute(
            text("SELECT state, error_code FROM parse_jobs WHERE id = :id"), {"id": old.id}
        ).one() == ("pending", "archive_integrity")
        assert db.scalar(text("SELECT count(*) FROM parse_runs")) == 0
        db.execute(
            text("UPDATE parse_jobs SET available_at = now() WHERE id = :id"), {"id": old.id}
        )
    archive.store(workbook.read_bytes())
    assert process_one(archive)
    assert complete(other, document)
    assert claim() is None
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM parse_runs")) == 2
        assert (
            db.scalar(text("SELECT count(*) FROM parsed_quantity_records WHERE quantity = 0")) == 2
        )
        assert (
            db.scalar(
                text("SELECT source_fields->'Qty'->>'cell' FROM parsed_quantity_records LIMIT 1")
            )
            == "Sheet!C5"
        )

    monkeypatch.setenv("RELAY_ARCHIVE_DIR", str(archive.root))
    path = "/api/v1/intake"
    with TestClient(app) as client:
        assert client.get(path + "/emails").status_code == 401
        signed_in(client, "viewer")
        inbox = client.get(path + "/emails").json()
        assert [record["id"] for record in inbox] == [second.mail_id, first.mail_id]
        detail = client.get(f"{path}/emails/{first.mail_id}").json()
        attachment = detail["attachments"][0]
        assert attachment["job_state"] == "succeeded"
        status = client.get("/api/v1/health/operations").json()
        assert status["parsing"]["succeeded"] == 2
        assert status["parsing"]["pending"] == status["parsing"]["expired"] == 0
        assert status["parsing"]["last_success_at"] is not None
        file_path = f"{path}/attachments/{attachment['id']}"
        assert client.get(file_path + "/file").content == workbook.read_bytes()
        rows = client.get(file_path + "/quantities").json()
        assert rows[0]["quantity_cell"] == "Sheet!C5"
        assert rows[0]["quantity"] == "0.00"
        with TestClient(app) as anonymous:
            assert anonymous.get(file_path + "/file").status_code == 401


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_workbench_classifies_real_jobs_and_preserves_source_coordinates(
    tmp_path: Path, signed_in: Callable[[TestClient, Role], User]
) -> None:
    archive = Archive(tmp_path / "workbench-archive")

    def receive_schedule(uid: int, subject: str, quantity: str) -> int:
        path = tmp_path / "schedule.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        for row in [
            ["4905 - Delivery Call Off"],
            ["NewTransmissionNo", "0001"],
            ["OrderNo", "001", "CustomerItemNo", "MAT-1", "QuantityUnit", "PCE"],
            ["DateTime", "Qty"],
            ["000000", quantity],
        ]:
            sheet.append(row)
        workbook.save(path)
        mail = EmailMessage()
        mail["From"] = "workbench@example.com"
        mail["Subject"] = subject
        mail.set_content("Attached schedule")
        mail.add_attachment(
            path.read_bytes(), maintype="application", subtype="octet-stream", filename="4905.xlsx"
        )
        return receive(mail.as_bytes(), _identity(uid), archive).mail_id

    base = "/api/v1/intake"
    with TestClient(app) as client:
        assert client.get(base + "/counts").status_code == 401
        signed_in(client, "viewer")
        before = client.get(base + "/counts").json()
        review_id = receive_schedule(23, "Workbench review", "18.0")
        failure_id = receive_schedule(24, "Workbench error", "bad")
        pending = client.get(base + "/emails", params={"state": "queued", "q": "4905.xlsx"})
        assert [row["id"] for row in pending.json()] == [failure_id, review_id]
        follow_up = {"state": "follow_up", "q": "WORKBENCH"}
        assert client.get(base + "/emails", params=follow_up).json() == []
        assert process_one(archive) and process_one(archive)
        counts = client.get(base + "/counts").json()
        assert counts == {
            "total": before["total"] + 2,
            "failed": before["failed"] + 1,
            "review": before["review"] + 1,
            "queued": before["queued"],
        }
        assert [row["id"] for row in client.get(base + "/emails", params=follow_up).json()] == [
            failure_id,
            review_id,
        ]
        assert (
            client.get(
                base + "/emails",
                params={"state": "actionable", "q": "WORKBENCH", "before": failure_id},
            ).json()[0]["id"]
            == review_id
        )
        review = client.get(f"{base}/emails/{review_id}").json()
        assert review["mail"]["templates"] == ["kroschu_4905"]
        assert review["mail"]["state"] == "review"
        attachment = review["attachments"][0]
        assert attachment["review_location"] == "Sheet!A5"
        row = client.get(f"{base}/attachments/{attachment['id']}/quantities").json()[0]
        assert row["raw_delivery_date"] == "000000"
        assert row["delivery_date_cell"] == "Sheet!A5"
        assert row["requested_date"] is None
        failure = client.get(f"{base}/emails/{failure_id}").json()
        assert failure["mail"]["state"] == "failed"
        assert failure["attachments"][0]["error_reason"] == "invalid quantity"
        assert failure["attachments"][0]["error_location"] == "Sheet!B5"


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
@pytest.mark.parametrize(
    ("address", "host"),
    [("test@example.com", "imap.263.net"), ("test@163.com", "imap.163.com")],
)
def test_imap_cursor_only_advances_past_durable_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    eml_and_workbook: tuple[bytes, Path],
    address: str,
    host: str,
) -> None:
    raw, _ = eml_and_workbook
    mailbox = SyncMailbox(address, host, "INBOX", date(2026, 11, 1), 5, "test")
    source = {"source": mailbox.source}
    archive = Archive(tmp_path / "sync-archive")
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    client.xatom.return_value = ("OK", [b"identified"])
    client.select.return_value = ("OK", [b"3"])
    client.response.return_value = ("UIDVALIDITY", [b"7"])
    disconnect = True

    def dispatch(command: str, *args: str) -> tuple[str, object]:
        if command == "SEARCH":
            assert args[-2:] == ("SINCE", "01-Nov-2026")
            return "OK", [b"20 21 22"]
        assert command == "FETCH"
        uid, query = args
        if query == "(UID RFC822.SIZE INTERNALDATE)":
            size = 99_999_999 if uid == "22" else len(raw)
            return "OK", [
                (
                    f'1 (UID {uid} RFC822.SIZE {size} INTERNALDATE "23-Nov-2026 10:00:00 +0800")'
                ).encode()
            ]
        assert query.startswith("(UID BODY.PEEK[]")
        assert uid != "22"  # The oversized message never downloads its body.
        if uid == "21" and disconnect:
            raise OSError("Simulated interruption")
        return "OK", [(f"1 (UID {uid} BODY[]<0> {{{len(raw)}}}".encode(), raw), b")"]

    client.uid.side_effect = dispatch
    monkeypatch.setattr(sync.imaplib, "IMAP4_SSL", factory)
    with pytest.raises(OSError, match="Simulated interruption"):
        sync_once(mailbox, archive)
    client.select.assert_called_once_with('"INBOX"', readonly=True)
    commands = [call[0] for call in client.method_calls]
    expected = ["login", "xatom", "select"] if host == "imap.163.com" else ["login", "select"]
    assert commands[: len(expected)] == expected
    with get_engine().connect() as db:
        cursor = text("SELECT last_uid FROM mail_sync_cursors WHERE source = :source")
        assert db.scalar(cursor, source) == 20

    disconnect = False
    assert sync_once(mailbox, archive) == 2
    assert sync_once(mailbox, archive) == 0
    with get_engine().connect() as db:
        assert db.scalar(cursor, source) == 22
        assert (
            db.scalar(
                text(
                    "SELECT intake_error_code FROM received_mail "
                    "WHERE source = :source AND uid = 22"
                ),
                source,
            )
            == "message_size"
        )
        assert (
            db.scalar(
                text("SELECT count(*) FROM received_mail WHERE source = :source"),
                source,
            )
            == 3
        )

    client.response.return_value = ("UIDVALIDITY", [b"8"])
    with pytest.raises(SyncResetRequired):
        sync_once(mailbox, archive)
    with get_engine().connect() as db:
        assert db.execute(
            text("SELECT uidvalidity, last_uid FROM mail_sync_cursors WHERE source = :source"),
            source,
        ).one() == (7, 22)
