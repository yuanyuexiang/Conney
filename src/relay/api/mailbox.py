from typing import Annotated

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from relay.api.auth import AdminUser, SignedInUser, require_browser_request
from relay.mail.configuration import (
    MailboxConfig,
    MailboxInput,
    MailboxSyncStatus,
    get_mail_settings,
    load_configuration,
    load_mailbox_credentials,
    record_connection_test,
    save_configuration,
    set_sync_enabled,
)
from relay.mail.connection import ConnectionResult, check_connection

router = APIRouter(prefix="/api/v1/mailbox", tags=["mailbox"])
_BROWSER = [Depends(require_browser_request)]


def mailbox_cipher(_: AdminUser) -> Fernet:
    try:
        return get_mail_settings().cipher()
    except ValueError:
        raise HTTPException(503, detail="mailbox_admin_unavailable") from None


MailboxCipher = Annotated[Fernet, Depends(mailbox_cipher)]


class SyncSettings(BaseModel):
    enabled: bool


def _read_configuration() -> MailboxConfig | None:
    try:
        return load_configuration()
    except SQLAlchemyError:
        raise HTTPException(503, detail="mailbox_storage_unavailable") from None


@router.get("")
def read_mailbox(_: AdminUser) -> MailboxConfig | None:
    return _read_configuration()


@router.get("/status")
def read_mailbox_status(_: SignedInUser) -> MailboxSyncStatus | None:
    configuration = _read_configuration()
    if configuration is None:
        return None
    return MailboxSyncStatus.model_validate(configuration.model_dump())


@router.put("", dependencies=_BROWSER)
def write_mailbox(values: MailboxInput, cipher: MailboxCipher) -> MailboxConfig:
    try:
        return save_configuration(values, cipher)
    except ValueError:
        raise HTTPException(409, detail="mailbox_credential_required") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="mailbox_storage_unavailable") from None


@router.post("/connection-test", dependencies=_BROWSER)
def connection_test(cipher: MailboxCipher) -> dict[str, ConnectionResult]:
    try:
        mailbox = load_mailbox_credentials(cipher)
    except InvalidToken:
        raise HTTPException(409, detail="mailbox_credential_invalid") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="mailbox_storage_unavailable") from None
    if mailbox is None or mailbox.revision is None:
        raise HTTPException(409, detail="mailbox_not_configured")
    result = check_connection(mailbox.address, mailbox.host, mailbox.folder, mailbox.password)
    if not record_connection_test(mailbox.revision, passed=result == "ok"):
        raise HTTPException(409, detail="mailbox_config_changed")
    return {"result": result}


@router.put("/sync", dependencies=_BROWSER)
def change_sync(values: SyncSettings, _: AdminUser) -> MailboxConfig:
    saved = set_sync_enabled(values.enabled)
    if saved is None:
        raise HTTPException(409, detail="mailbox_test_required")
    return saved
