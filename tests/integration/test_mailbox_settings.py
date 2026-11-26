import os
import smtplib
import sys
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

import relay.api.mailbox as mailbox_api
from relay.api.app import app
from relay.auth.accounts import Role, User
from relay.database.session import get_engine
from relay.mail.configuration import enabled_sync_revision, get_mail_settings, record_sync_result
from relay.workers import mail as mail_worker

_BROWSER = {"X-Relay-Request": "1"}


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_outgoing_uses_saved_incoming_credential_and_requires_retest_after_rotation(
    monkeypatch: pytest.MonkeyPatch,
    signed_in: Callable[[TestClient, Role], User],
) -> None:
    monkeypatch.setenv("RELAY_MAIL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_mail_settings.cache_clear()
    smtp = MagicMock()
    monkeypatch.setattr(smtplib, "SMTP_SSL", MagicMock(return_value=smtp))
    incoming = "/api/v1/mailbox"
    outgoing = "/api/v1/mailbox/outgoing"
    account = {
        "address": "shared-test@163.com",
        "host": "imap.163.com",
        "folder": "INBOX",
        "since": "2026-11-01",
        "poll_minutes": 5,
        "password": "test-only-shared-code",
    }
    link = {
        "address": account["address"],
        "host": "smtp.163.com",
        "credential_source": "incoming",
        "domains": [],
        "test_recipient": "receiver@example.com",
    }
    try:
        with TestClient(app, headers=_BROWSER) as client:
            signed_in(client, "admin")
            assert client.put(outgoing, json=link).status_code == 409
            assert client.put(incoming, json=account).status_code == 200
            wrong_account = client.put(outgoing, json={**link, "address": "other@163.com"})
            assert wrong_account.status_code == 409
            assert client.put(outgoing, json={**link, "password": "test-extra"}).status_code == 422
            saved = client.put(outgoing, json=link)
            assert saved.status_code == 200
            assert saved.json()["credential_source"] == "incoming"
            assert saved.json()["credential_available"] and not saved.json()["verified"]
            assert account["password"] not in saved.text
            assert client.post(outgoing + "/connection-test").json() == {"result": "ok"}
            smtp.login.assert_called_once_with(account["address"], account["password"])
            smtp.mail.assert_not_called()
            smtp.rcpt.assert_not_called()
            smtp.data.assert_not_called()

            new_scope = client.put(incoming, json={**account, "password": None, "poll_minutes": 8})
            assert new_scope.status_code == 200
            after_scope = client.get(outgoing).json()
            assert after_scope["revision"] == saved.json()["revision"] and after_scope["verified"]

            new_code = "test-only-rotated-code"
            assert client.put(incoming, json={**account, "password": new_code}).status_code == 200
            rotated = client.get(outgoing).json()
            assert rotated["revision"] != saved.json()["revision"] and not rotated["verified"]
            assert client.post(outgoing + "/connection-test").json() == {"result": "ok"}
            smtp.login.assert_called_with(account["address"], new_code)

            replacement = client.put(incoming, json={**account, "address": "replacement@163.com"})
            assert replacement.is_success
            detached = client.get(outgoing).json()
            assert detached["address"] == account["address"]
            assert not detached["credential_available"]
            assert client.post(outgoing + "/connection-test").status_code == 409
            standalone = {**link, "credential_source": "separate"}
            assert client.put(outgoing, json=standalone).status_code == 409
            assert client.put(outgoing, json={**standalone, "password": "test-separate"}).is_success
            assert client.post(outgoing + "/connection-test").json() == {"result": "ok"}
            smtp.login.assert_called_with(account["address"], "test-separate")
    finally:
        with get_engine().begin() as connection:
            connection.execute(text("DELETE FROM outgoing_mailbox"))
            connection.execute(text("DELETE FROM mailbox_settings"))
        get_mail_settings.cache_clear()


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
@pytest.mark.parametrize(
    ("address", "host"),
    [("qa-relay@example.com", "imap.263.net"), ("qa-relay@163.com", "imap.163.com")],
)
def test_mailbox_config_is_protected_and_never_returns_the_credential(
    monkeypatch: pytest.MonkeyPatch,
    signed_in: Callable[[TestClient, Role], User],
    address: str,
    host: str,
) -> None:
    key = Fernet.generate_key()
    monkeypatch.setenv("RELAY_MAIL_ENCRYPTION_KEY", key.decode())
    check = MagicMock(return_value="ok")
    monkeypatch.setattr(mailbox_api, "check_connection", check)
    get_mail_settings.cache_clear()

    credential = "test-only-imap-authorization-code"
    headers = _BROWSER
    payload = {
        "address": address,
        "host": host,
        "folder": "INBOX",
        "since": "2026-11-01",
        "poll_minutes": 5,
        "password": credential,
    }
    with get_engine().connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM mailbox_settings")) == 0

    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/mailbox").status_code == 401
            assert client.get("/api/v1/mailbox/status").status_code == 401
            assert client.put("/api/v1/mailbox", headers=headers, json=payload).status_code == 401
            signed_in(client, "admin")
            assert client.get("/api/v1/mailbox", headers=headers).json() is None

            invalid = client.put(
                "/api/v1/mailbox", headers=headers, json={**payload, "password": [credential]}
            )
            assert invalid.status_code == 422
            assert credential not in invalid.text
            wrong_host = "imap.263.net" if host == "imap.163.com" else "imap.163.com"
            mismatch = client.put(
                "/api/v1/mailbox", headers=headers, json={**payload, "host": wrong_host}
            )
            assert mismatch.status_code == 422
            assert credential not in mismatch.text

            saved = client.put("/api/v1/mailbox", headers=headers, json=payload)
            assert saved.status_code == 200
            assert saved.json()["has_password"] is True
            assert saved.headers["cache-control"] == "no-store"
            assert credential not in saved.text
            assert "password" not in saved.json()

            with get_engine().connect() as connection:
                encrypted = connection.scalar(
                    text("SELECT encrypted_password FROM mailbox_settings")
                )
            assert bytes(encrypted) != credential.encode()
            assert Fernet(key).decrypt(bytes(encrypted)) == credential.encode()

            updated = client.put(
                "/api/v1/mailbox",
                headers=headers,
                json={**payload, "poll_minutes": 10, "password": None},
            )
            assert updated.status_code == 200
            assert updated.json()["poll_minutes"] == 10
            assert client.get("/api/v1/mailbox", headers=headers).json() == updated.json()

            changed = client.put(
                "/api/v1/mailbox",
                headers=headers,
                json={**payload, "address": "other-" + address, "password": None},
            )
            assert changed.status_code == 409

            blocked = client.put("/api/v1/mailbox/sync", headers=headers, json={"enabled": True})
            assert blocked.status_code == 409
            result = client.post("/api/v1/mailbox/connection-test", headers=headers)
            assert result.json() == {"result": "ok"}
            check.assert_called_once_with(address, host, "INBOX", credential)
            enabled = client.put("/api/v1/mailbox/sync", headers=headers, json={"enabled": True})
            assert enabled.status_code == 200 and enabled.json()["enabled"]
            with TestClient(app) as viewer:
                signed_in(viewer, "viewer")
                assert viewer.get("/api/v1/mailbox").status_code == 403
                status = viewer.get("/api/v1/mailbox/status")
                assert status.status_code == 200
                assert status.headers["cache-control"] == "no-store"
                assert status.json() == {
                    "enabled": True,
                    "verified": True,
                    "last_sync_at": None,
                    "last_sync_result": None,
                    "last_sync_count": None,
                }
                operations = viewer.get("/api/v1/health/operations").json()
                assert operations["mailbox"] == {**status.json(), "poll_minutes": 10}

            monkeypatch.setattr(sys, "argv", ["relay.workers.mail", "--once"])
            settings = MagicMock()
            settings.cipher.side_effect = ValueError
            monkeypatch.setattr(mail_worker, "get_mail_settings", lambda: settings)
            with pytest.raises(SystemExit, match="1"):
                mail_worker.main()
            assert client.get("/api/v1/mailbox/status").json()["last_sync_result"] == "sync_failed"

            revision = enabled_sync_revision()
            assert revision is not None
            record_sync_result(revision, "ok", 2)
            assert client.get("/api/v1/mailbox/status").json()["last_sync_count"] == 2
            record_sync_result(revision, "uidvalidity_changed", None)
            paused = client.get("/api/v1/mailbox/status").json()
            assert paused["last_sync_result"] == "uidvalidity_changed"
            assert paused["enabled"] is False

            edited = client.put(
                "/api/v1/mailbox",
                headers=headers,
                json={**payload, "poll_minutes": 15, "password": None},
            )
            assert edited.status_code == 200
            assert not edited.json()["enabled"] and not edited.json()["verified"]
            assert (
                client.put(
                    "/api/v1/mailbox/sync", headers=headers, json={"enabled": True}
                ).status_code
                == 409
            )
    finally:
        with get_engine().begin() as connection:
            connection.execute(
                text("DELETE FROM mailbox_settings WHERE address = :address"),
                {
                    "address": address,
                },
            )
        get_mail_settings.cache_clear()
