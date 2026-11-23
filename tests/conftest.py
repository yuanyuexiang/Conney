from collections.abc import Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from relay.auth.accounts import NewUser, Role, User, create_user

BROWSER = {"X-Relay-Request": "1"}
TEMP_PASSWORD = "initial-test-password"
USER_PASSWORD = "updated-test-password"


@pytest.fixture
def signed_in() -> Callable[[TestClient, Role], User]:
    def sign_in(client: TestClient, role: Role) -> User:
        username = f"{role}-{uuid4().hex[:12]}"
        user = create_user(NewUser(username=username, role=role, password=SecretStr(TEMP_PASSWORD)))
        assert user is not None
        signed = client.post(
            "/api/v1/auth/login",
            headers=BROWSER,
            json={"username": username, "password": TEMP_PASSWORD},
        )
        assert signed.status_code == 200
        changed = client.post(
            "/api/v1/auth/password",
            headers=BROWSER,
            json={"current_password": TEMP_PASSWORD, "new_password": USER_PASSWORD},
        )
        assert changed.status_code == 200
        return User.model_validate(changed.json())

    return sign_in
