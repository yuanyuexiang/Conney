import os
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from relay.api.app import app
from relay.auth.accounts import Role, User
from relay.database.session import get_engine
from relay.mail.intake import MailIdentity, receive
from relay.storage.archive import Archive
from relay.workers.heartbeat import WorkerHeartbeat


def test_live_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_operations_requires_a_session() -> None:
    with TestClient(app) as client:
        assert client.get("/api/v1/health/operations").status_code == 401
        assert client.get("/api/v1/dashboard").status_code == 401


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_dashboard_counts_shanghai_calendar_days_and_recent_receipts(
    signed_in: Callable[[TestClient, Role], User],
) -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    today = datetime.now(shanghai).date()
    midnight = datetime.combine(today, time.min, shanghai)
    timestamps = [midnight, midnight - timedelta(seconds=1), midnight - timedelta(days=7)]
    engine = get_engine()
    with engine.begin() as connection:
        for uid, timestamp in enumerate(timestamps, start=1):
            connection.execute(
                text(
                    "INSERT INTO received_mail (source, folder, uidvalidity, uid, received_at, "
                    "subject, sender) VALUES ('local:dashboard-test', 'INBOX', 1, :uid, "
                    ":received_at, :subject, 'test@example.test')"
                ),
                {"uid": uid, "received_at": timestamp, "subject": f"Dashboard test {uid}"},
            )
    try:
        with TestClient(app) as client:
            signed_in(client, "viewer")
            response = client.get("/api/v1/dashboard")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            result = response.json()
            assert [entry["date"] for entry in result["receipts"]] == [
                (today - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)
            ]
            assert [entry["received"] for entry in result["receipts"]] == [0, 0, 0, 0, 0, 1, 1]
            assert [mail["subject"] for mail in result["recent_mail"]] == [
                "Dashboard test 1",
                "Dashboard test 2",
                "Dashboard test 3",
            ]
            assert result["recent_mail"][0]["state"] == "archived"
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM received_mail WHERE source = 'local:dashboard-test'")
            )


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_ready_with_migrated_postgresql() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_operations_checks_real_worker_heartbeats_and_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signed_in: Callable[[TestClient, Role], User],
) -> None:
    archive = Archive(tmp_path)
    monkeypatch.setenv("RELAY_ARCHIVE_DIR", str(archive.root))
    heartbeat = WorkerHeartbeat("mail")
    heartbeat()
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO worker_heartbeats (worker, seen_at) "
                "VALUES ('parser', now() - interval '2 minutes') "
                "ON CONFLICT (worker) DO UPDATE SET seen_at = EXCLUDED.seen_at"
            )
        )
    message = EmailMessage()
    message.set_content("Ops probe")
    message.add_attachment(b"sample", maintype="text", subtype="plain", filename="probe.txt")
    identity = MailIdentity("local:operations-test", "INBOX", 1, 1, datetime.now(UTC))
    expected = archive.store(b"sample")
    archived_file = archive.verified_path(expected.sha256, expected.size_bytes)
    result = receive(message.as_bytes(), identity, archive)
    try:
        with TestClient(app) as client:
            signed_in(client, "viewer")
            status = client.get("/api/v1/health/operations")
            assert status.status_code == 200
            assert status.headers["cache-control"] == "no-store"
            body = status.json()
            assert body["mail_worker"]["state"] == "reporting"
            assert body["parser_worker"]["state"] == "stale"
            assert body["archive"] == {"state": "verified"}
            assert str(archive.root) not in status.text
            assert expected.sha256 not in status.text

            archived_file.chmod(0o600)
            archived_file.write_bytes(b"wrong!")
            assert client.get("/api/v1/health/operations").json()["archive"] == {"state": "damaged"}
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM received_attachments WHERE mail_id = :id"), {"id": result.mail_id}
            )
            connection.execute(
                text("DELETE FROM received_mail WHERE id = :id"), {"id": result.mail_id}
            )
            connection.execute(
                text("DELETE FROM archive_objects WHERE sha256 = :sha256"),
                {"sha256": expected.sha256},
            )
