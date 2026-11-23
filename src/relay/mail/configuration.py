from datetime import date, datetime
from functools import lru_cache
from typing import Literal
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from relay.database.session import get_engine
from relay.mail.sync import SyncMailbox

MailboxHost = Literal["imap.263.net", "imapw.263.net"]


class MailSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RELAY_MAIL_", env_file=".env", extra="ignore")

    encryption_key: SecretStr = SecretStr("")

    def cipher(self) -> Fernet:
        return Fernet(self.encryption_key.get_secret_value().encode())


@lru_cache
def get_mail_settings() -> MailSettings:
    return MailSettings()


class MailboxInput(BaseModel):
    address: EmailStr = Field(max_length=254)
    host: MailboxHost = "imap.263.net"
    folder: str = Field(min_length=1, max_length=128)
    since: date
    poll_minutes: int = Field(ge=1, le=60)
    password: SecretStr | None = None

    @field_validator("folder")
    @classmethod
    def checked_folder(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("Invalid mailbox folder")
        return value

    @field_validator("password")
    @classmethod
    def checked_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        password = value.get_secret_value()
        if len(password) > 512 or not password.isascii() or not password.isprintable():
            raise ValueError("Invalid mailbox credential")
        return value


class MailboxSyncStatus(BaseModel):
    enabled: bool
    verified: bool
    last_sync_at: datetime | None = None
    last_sync_result: str | None = None
    last_sync_count: int | None = None


class MailboxConfig(MailboxSyncStatus):
    address: str
    host: MailboxHost
    folder: str
    since: date
    poll_minutes: int
    has_password: bool


_PUBLIC_COLUMNS = (
    "address, host, folder, since, poll_minutes, enabled, "
    "COALESCE(tested_revision = revision, false) AS verified, "
    "last_sync_at, last_sync_result, last_sync_count"
)
_RESET = (
    "revision = :revision, tested_revision = NULL, enabled = false, "
    "last_sync_at = NULL, last_sync_result = NULL, last_sync_count = NULL "
)


def load_configuration() -> MailboxConfig | None:
    with get_engine().connect() as connection:
        row = (
            connection.execute(text(f"SELECT {_PUBLIC_COLUMNS} FROM mailbox_settings WHERE id = 1"))
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    return MailboxConfig.model_validate({**row, "has_password": True})


def save_configuration(values: MailboxInput, cipher: Fernet) -> MailboxConfig:
    fields = values.model_dump(exclude={"password"})
    params = {**fields, "revision": uuid4()}
    with get_engine().begin() as connection:
        if values.password is None:
            result = connection.execute(
                text(
                    "UPDATE mailbox_settings SET folder = :folder, since = :since, "
                    "poll_minutes = :poll_minutes, "
                    + _RESET
                    + "WHERE id = 1 AND address = :address AND host = :host RETURNING id"
                ),
                params,
            )
            if result.scalar_one_or_none() is None:
                raise ValueError("A credential is required for a new account or server.")
        else:
            encrypted = cipher.encrypt(values.password.get_secret_value().encode())
            connection.execute(
                text(
                    "INSERT INTO mailbox_settings (id, address, host, folder, since, poll_minutes, "
                    "encrypted_password, revision) VALUES (1, :address, :host, :folder, :since, "
                    ":poll_minutes, :encrypted_password, :revision) ON CONFLICT (id) DO UPDATE SET "
                    "address = EXCLUDED.address, host = EXCLUDED.host, folder = EXCLUDED.folder, "
                    "since = EXCLUDED.since, poll_minutes = EXCLUDED.poll_minutes, "
                    "encrypted_password = EXCLUDED.encrypted_password, " + _RESET
                ),
                {**params, "encrypted_password": encrypted},
            )
    return MailboxConfig.model_validate(
        {**fields, "has_password": True, "enabled": False, "verified": False}
    )


def load_mailbox_credentials(cipher: Fernet, *, enabled_only: bool = False) -> SyncMailbox | None:
    query = (
        "SELECT address, host, folder, since, poll_minutes, encrypted_password, revision "
        "FROM mailbox_settings WHERE id = 1"
    )
    if enabled_only:
        query += " AND enabled"
    with get_engine().connect() as connection:
        row = connection.execute(text(query)).one_or_none()
    if row is None:
        return None
    return SyncMailbox(
        row.address,
        row.host,
        row.folder,
        row.since,
        row.poll_minutes,
        cipher.decrypt(bytes(row.encrypted_password)).decode(),
        revision=row.revision,
    )


def is_sync_enabled(revision: UUID | None = None) -> bool:
    with get_engine().connect() as connection:
        enabled = connection.scalar(
            text(
                "SELECT enabled FROM mailbox_settings WHERE id = 1 "
                "AND (CAST(:revision AS uuid) IS NULL OR revision = :revision)"
            ),
            {"revision": revision},
        )
    return enabled is True


def enabled_sync_revision() -> UUID | None:
    with get_engine().connect() as connection:
        revision: UUID | None = connection.scalar(
            text("SELECT revision FROM mailbox_settings WHERE id = 1 AND enabled")
        )
    return revision


def record_connection_test(revision: UUID, *, passed: bool) -> bool:
    with get_engine().begin() as connection:
        updated = connection.execute(
            text(
                "UPDATE mailbox_settings SET tested_revision = CASE WHEN :passed THEN revision "
                "ELSE NULL END, enabled = (enabled AND :passed) "
                "WHERE id = 1 AND revision = :revision "
                "RETURNING id"
            ),
            {"revision": revision, "passed": passed},
        ).scalar_one_or_none()
    return updated is not None


def set_sync_enabled(enabled: bool) -> MailboxConfig | None:
    with get_engine().begin() as connection:
        row = (
            connection.execute(
                text(
                    "UPDATE mailbox_settings SET enabled = :enabled WHERE id = 1 "
                    "AND (NOT :enabled OR (tested_revision = revision "
                    "AND last_sync_result IS DISTINCT FROM 'uidvalidity_changed')) "
                    f"RETURNING {_PUBLIC_COLUMNS}"
                ),
                {"enabled": enabled},
            )
            .mappings()
            .one_or_none()
        )
    return None if row is None else MailboxConfig.model_validate({**row, "has_password": True})


def record_sync_result(revision: UUID, result: str, count: int | None) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "UPDATE mailbox_settings SET last_sync_at = now(), last_sync_result = :result, "
                "last_sync_count = :count, enabled = enabled AND NOT :pause "
                "WHERE id = 1 AND revision = :revision"
            ),
            {
                "result": result,
                "count": count,
                "revision": revision,
                "pause": result == "uidvalidity_changed",
            },
        )
