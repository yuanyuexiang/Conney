from hashlib import scrypt, sha256
from hmac import compare_digest
from secrets import token_bytes, token_urlsafe
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Connection

from relay.database.session import get_engine

Role = Literal["admin", "viewer"]
SESSION_SECONDS = 12 * 60 * 60
USER_COLUMNS = "id, username, role, active, must_change_password"
USERNAME_PATTERN = r"^[a-z][a-z0-9_.-]{2,63}$"
_DUMMY_HASH = bytes(48)


class User(BaseModel):
    id: int
    username: str
    role: Role
    active: bool
    must_change_password: bool


class NewUser(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    role: Role
    password: SecretStr = Field(min_length=8, max_length=128)


class ChangeUser(BaseModel):
    role: Role
    active: bool


def _derive(password: str, salt: bytes) -> bytes:
    return scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024**2, dklen=32)


def _hash(password: str) -> bytes:
    salt = token_bytes(16)
    return salt + _derive(password, salt)


def _matches(password: str, stored: bytes) -> bool:
    return compare_digest(_derive(password, stored[:16]), stored[16:])


def _session_hash(token: str) -> bytes:
    return sha256(token.encode()).digest()


def _new_session(connection: Connection, user_id: int) -> str:
    token = token_urlsafe(32)
    connection.execute(text("DELETE FROM relay_sessions WHERE expires_at <= now()"))
    connection.execute(
        text(
            "INSERT INTO relay_sessions (token_hash, user_id, expires_at) "
            "VALUES (:token, :id, now() + make_interval(secs => :duration))"
        ),
        {"token": _session_hash(token), "id": user_id, "duration": SESSION_SECONDS},
    )
    return token


def login(username: str, password: str) -> tuple[User, str] | None:
    with get_engine().connect() as connection:
        row = connection.execute(
            text("SELECT id, password_hash FROM relay_users WHERE username = :username"),
            {"username": username.strip().lower()},
        ).one_or_none()

    credential = row.password_hash if row else _DUMMY_HASH
    valid = _matches(password, credential)
    if row is None or not valid:
        return None

    with get_engine().begin() as connection:
        current = (
            connection.execute(
                text(
                    f"SELECT {USER_COLUMNS} FROM relay_users WHERE id = :id AND active "
                    "AND password_hash = :password_hash FOR UPDATE"
                ),
                {"id": row.id, "password_hash": credential},
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            return None
        user = User.model_validate(current)
        return user, _new_session(connection, user.id)


def session_user(token: str) -> User | None:
    if not 32 <= len(token) <= 64:
        return None
    with get_engine().connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT u.id, u.username, u.role, u.active, u.must_change_password "
                    "FROM relay_sessions AS s JOIN relay_users AS u ON u.id = s.user_id "
                    "WHERE s.token_hash = :token AND s.expires_at > now() AND u.active"
                ),
                {"token": _session_hash(token)},
            )
            .mappings()
            .one_or_none()
        )
    return None if row is None else User.model_validate(row)


def revoke_session(token: str) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text("DELETE FROM relay_sessions WHERE token_hash = :token"),
            {"token": _session_hash(token)},
        )


def update_own_password(user: User, current: str, replacement: str) -> str | None:
    if current == replacement:
        raise ValueError("password_unchanged")
    with get_engine().begin() as connection:
        old_hash = connection.execute(
            text("SELECT password_hash FROM relay_users WHERE id = :id AND active FOR UPDATE"),
            {"id": user.id},
        ).scalar_one_or_none()
        if old_hash is None or not _matches(current, old_hash):
            return None
        connection.execute(
            text(
                "UPDATE relay_users SET password_hash = :password, must_change_password = false "
                "WHERE id = :id"
            ),
            {"password": _hash(replacement), "id": user.id},
        )
        connection.execute(text("DELETE FROM relay_sessions WHERE user_id = :id"), {"id": user.id})
        return _new_session(connection, user.id)


def create_user(values: NewUser, *, bootstrap: bool = False) -> User | None:
    password_hash = _hash(values.password.get_secret_value())
    with get_engine().begin() as connection:
        if bootstrap:
            connection.execute(text("LOCK TABLE relay_users IN SHARE ROW EXCLUSIVE MODE"))
            if connection.scalar(text("SELECT EXISTS (SELECT 1 FROM relay_users)")):
                return None
        row = (
            connection.execute(
                text(
                    "INSERT INTO relay_users (username, role, password_hash) "
                    "VALUES (:username, :role, :password) ON CONFLICT (username) DO NOTHING "
                    f"RETURNING {USER_COLUMNS}"
                ),
                {"username": values.username, "role": values.role, "password": password_hash},
            )
            .mappings()
            .one_or_none()
        )
    return None if row is None else User.model_validate(row)


def list_users() -> list[User]:
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(f"SELECT {USER_COLUMNS} FROM relay_users ORDER BY id")
        ).mappings()
        return [User.model_validate(row) for row in rows]


def update_user(actor: User, user_id: int, changes: ChangeUser) -> User | None:
    if actor.id == user_id and (not changes.active or changes.role != actor.role):
        raise ValueError("cannot_change_own_access")
    with get_engine().begin() as connection:
        # Serialize role/activation changes, and lock the actor against concurrent disablement.
        connection.execute(text("LOCK TABLE relay_users IN SHARE ROW EXCLUSIVE MODE"))
        authorized = connection.scalar(
            text("SELECT id FROM relay_users WHERE id = :id AND active AND role = 'admin'"),
            {"id": actor.id},
        )
        if authorized is None:
            raise ValueError("permission_denied")
        row = (
            connection.execute(
                text(
                    "UPDATE relay_users SET role = :role, active = :active WHERE id = :id "
                    f"RETURNING {USER_COLUMNS}"
                ),
                {"id": user_id, **changes.model_dump()},
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            connection.execute(
                text("DELETE FROM relay_sessions WHERE user_id = :id"),
                {"id": user_id},
            )
    return None if row is None else User.model_validate(row)


def reset_password(actor: User, user_id: int, replacement: str) -> bool:
    if actor.id == user_id:
        raise ValueError("use_own_password_change")
    password = _hash(replacement)
    with get_engine().begin() as connection:
        updated = connection.execute(
            text(
                "UPDATE relay_users SET password_hash = :password, must_change_password = true "
                "WHERE id = :id RETURNING id"
            ),
            {"password": password, "id": user_id},
        ).scalar_one_or_none()
        if updated is None:
            return False
        connection.execute(text("DELETE FROM relay_sessions WHERE user_id = :id"), {"id": user_id})
        return True
