import os
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
from relay.mail.configuration import get_mail_settings

_BROWSER = {"X-Relay-Request": "1"}


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_mailbox_config_is_protected_and_never_returns_the_credential(
    monkeypatch: pytest.MonkeyPatch,
    signed_in: Callable[[TestClient, Role], User],
) -> None:
    key = Fernet.generate_key()
    monkeypatch.setenv("RELAY_MAIL_ENCRYPTION_KEY", key.decode())
    check = MagicMock(return_value="ok")
    monkeypatch.setattr(mailbox_api, "check_connection", check)
    get_mail_settings.cache_clear()

    address = "qa-relay@example.com"
    credential = "test-only-263-authorization-code"
    headers = _BROWSER
    payload = {
        "address": address,
        "host": "imap.263.net",
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
            assert client.put("/api/v1/mailbox", headers=headers, json=payload).status_code == 401
            signed_in(client, "admin")
            assert client.get("/api/v1/mailbox", headers=headers).json() is None

            invalid = client.put(
                "/api/v1/mailbox", headers=headers, json={**payload, "password": [credential]}
            )
            assert invalid.status_code == 422
            assert credential not in invalid.text

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
                json={**payload, "address": "another@example.com", "password": None},
            )
            assert changed.status_code == 409

            blocked = client.put("/api/v1/mailbox/sync", headers=headers, json={"enabled": True})
            assert blocked.status_code == 409
            result = client.post("/api/v1/mailbox/connection-test", headers=headers)
            assert result.json() == {"result": "ok"}
            check.assert_called_once_with(address, "imap.263.net", "INBOX", credential)
            enabled = client.put("/api/v1/mailbox/sync", headers=headers, json={"enabled": True})
            assert enabled.status_code == 200 and enabled.json()["enabled"]
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
