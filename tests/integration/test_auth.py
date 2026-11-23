import os
from collections.abc import Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from relay.api.app import app
from relay.auth.accounts import Role, User

BROWSER = {"X-Relay-Request": "1"}


@pytest.mark.skipif(os.environ.get("RELAY_TEST_DB") != "1", reason="Requires test PostgreSQL")
def test_access_lifecycle(signed_in: Callable[[TestClient, Role], User]) -> None:
    temporary = "temporary-user-password"
    permanent = "replacement-user-password"
    username = f"reader-{uuid4().hex[:12]}"

    with TestClient(app) as admin, TestClient(app) as viewer:
        assert viewer.get("/api/v1/auth/me").status_code == 401
        assert (
            admin.post(
                "/api/v1/auth/login", json={"username": "admin", "password": temporary}
            ).status_code
            == 403
        )
        current = signed_in(admin, "admin")
        self_edit = admin.patch(
            f"/api/v1/users/{current.id}",
            headers=BROWSER,
            json={"role": "viewer", "active": False},
        )
        assert self_edit.status_code == 409
        created = admin.post(
            "/api/v1/users",
            headers=BROWSER,
            json={"username": username, "role": "viewer", "password": temporary},
        )
        assert created.status_code == 201 and temporary not in created.text
        assert created.json()["must_change_password"]
        user_id = created.json()["id"]

        logged_in = viewer.post(
            "/api/v1/auth/login",
            headers=BROWSER,
            json={"username": username, "password": temporary},
        )
        assert logged_in.status_code == 200
        cookie = logged_in.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert viewer.get("/api/v1/intake/emails").status_code == 403
        changed = viewer.post(
            "/api/v1/auth/password",
            headers=BROWSER,
            json={"current_password": temporary, "new_password": permanent},
        )
        assert changed.status_code == 200 and not changed.json()["must_change_password"]
        assert viewer.get("/api/v1/intake/emails").status_code == 200
        assert viewer.get("/api/v1/mailbox").status_code == 403
        assert viewer.get("/api/v1/users").status_code == 403

        reset = admin.post(
            f"/api/v1/users/{user_id}/password", headers=BROWSER, json={"password": temporary}
        )
        assert reset.status_code == 204
        assert viewer.get("/api/v1/auth/me").status_code == 401

        disabled = admin.patch(
            f"/api/v1/users/{user_id}",
            headers=BROWSER,
            json={"role": "viewer", "active": False},
        )
        assert disabled.status_code == 200 and not disabled.json()["active"]
        assert (
            viewer.post(
                "/api/v1/auth/login",
                headers=BROWSER,
                json={"username": username, "password": temporary},
            ).status_code
            == 401
        )
        assert admin.post("/api/v1/auth/logout", headers=BROWSER).status_code == 204
        assert admin.get("/api/v1/auth/me").status_code == 401
