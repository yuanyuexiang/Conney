from typing import Annotated
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from relay.api.auth import AdminUser, require_browser_request
from relay.api.delivery import _draft_row
from relay.api.mailbox import MailboxCipher
from relay.database.session import get_engine
from relay.mail.outgoing import (
    IncomingMailboxUnavailable,
    OutgoingConfig,
    OutgoingInput,
    check_outgoing,
    decrypt_credentials,
    read_credentials,
    read_outgoing,
    save_outgoing,
)
from relay.services.dispatch import (
    DispatchError,
    DispatchRecord,
    DispatchRequest,
    GroupEdit,
    GroupInput,
    NewDispatch,
    RecipientGroup,
    attempted_addresses,
    dispatch_history,
    eligible_groups,
    queue_dispatch,
    read_groups,
)

router = APIRouter(prefix="/api/v1", tags=["dispatch"])
_BROWSER = [Depends(require_browser_request)]
_DISPATCH = "/delivery/{run_id}/releases/{version}/dispatches"
_GROUPS = "/mailbox/recipient-groups"


class SendOptions(BaseModel):
    sender: str | None
    mailbox_revision: UUID | None
    verified: bool
    test_mode: bool
    groups: list[RecipientGroup]
    attempted_addresses: list[str]


@router.get("/mailbox/outgoing")
def outgoing_settings(_: AdminUser) -> OutgoingConfig | None:
    with get_engine().connect() as connection:
        return read_outgoing(connection)


@router.put("/mailbox/outgoing", dependencies=_BROWSER)
def set_outgoing(values: OutgoingInput, cipher: MailboxCipher) -> OutgoingConfig:
    try:
        saved = save_outgoing(values, cipher)
    except IncomingMailboxUnavailable:
        raise HTTPException(409, detail="outgoing_incoming_unavailable") from None
    if saved is None:
        raise HTTPException(409, detail="outgoing_credential_required")
    return saved


@router.post("/mailbox/outgoing/connection-test", dependencies=_BROWSER)
def test_outgoing(cipher: MailboxCipher) -> dict[str, str]:
    row = read_credentials()
    if row is None:
        raise HTTPException(409, detail="outgoing_not_configured")
    if not row.credential_available:
        raise HTTPException(409, detail="outgoing_incoming_unavailable")
    try:
        credentials = decrypt_credentials(row, cipher)
    except InvalidToken:
        raise HTTPException(409, detail="mailbox_credential_invalid") from None
    result = check_outgoing(credentials)
    passed = result.state == "accepted"
    with get_engine().begin() as connection:
        updated = connection.scalar(
            text("""
                UPDATE outgoing_mailbox SET verified = :passed
                WHERE id = 1 AND revision = :revision RETURNING id
            """),
            {"passed": passed, "revision": credentials.revision},
        )
    if updated is None:
        raise HTTPException(409, detail="outgoing_changed")
    return {"result": "ok" if passed else (result.detail or "smtp_unreachable")}


@router.get(_GROUPS)
def recipient_groups(_: AdminUser) -> list[RecipientGroup]:
    with get_engine().connect() as connection:
        return read_groups(connection)


def _save_group(values: GroupInput, group_id: int | None = None) -> RecipientGroup:
    with get_engine().begin() as connection:
        config = read_outgoing(connection, lock=True)
        if values.active and (
            config is None or any(not config.allows(address) for address in values.addresses)
        ):
            raise HTTPException(422, detail="dispatch_address_not_allowed")
        if isinstance(values, GroupEdit):
            statement = """
                UPDATE delivery_recipient_groups SET name = :name, addresses = :addresses,
                    active = :active, revision = revision + 1
                WHERE id = :id AND revision = :revision RETURNING *
            """
        else:
            statement = """
                INSERT INTO delivery_recipient_groups (name, addresses, active)
                VALUES (:name, :addresses, :active) RETURNING *
            """
        try:
            row = (
                connection.execute(text(statement), {**values.model_dump(), "id": group_id})
                .mappings()
                .one_or_none()
            )
        except IntegrityError:
            raise HTTPException(409, detail="dispatch_group_exists") from None
        if row is None:
            raise HTTPException(409, detail="dispatch_groups_changed")
        return RecipientGroup.model_validate(row)


@router.post(_GROUPS, dependencies=_BROWSER, status_code=201)
def add_recipient_group(values: GroupInput, _: AdminUser) -> RecipientGroup:
    return _save_group(values)


@router.put(_GROUPS + "/{group_id}", dependencies=_BROWSER)
def edit_recipient_group(group_id: int, values: GroupEdit, _: AdminUser) -> RecipientGroup:
    return _save_group(values, group_id)


def _release_id(connection: Connection, run_id: int, version: int, *, latest: bool = False) -> int:
    if latest:
        # The draft row serializes confirmation and queue creation for this batch.
        _draft_row(connection, run_id, lock=True)
        release = connection.execute(
            text("""
            SELECT id, version FROM delivery_releases
            WHERE run_id = :run_id ORDER BY version DESC LIMIT 1
        """),
            {"run_id": run_id},
        ).one_or_none()
        if release and release.version != version:
            raise HTTPException(409, detail="release_superseded")
        value = release.id if release else None
    else:
        value = connection.scalar(
            text("""
            SELECT id FROM delivery_releases WHERE run_id = :run_id AND version = :version
        """),
            {"run_id": run_id, "version": version},
        )
    if value is None:
        raise HTTPException(404, detail="release_not_found")
    return int(value)


@router.get(_DISPATCH + "/options")
def send_options(run_id: int, version: int, _: AdminUser) -> SendOptions:
    with get_engine().connect() as connection:
        release = _release_id(connection, run_id, version)
        config = read_outgoing(connection)
        return SendOptions(
            sender=config.address if config else None,
            verified=bool(config and config.verified),
            mailbox_revision=config.revision if config and config.verified else None,
            test_mode=bool(config and config.test_recipient),
            groups=eligible_groups(connection, config) if config else [],
            attempted_addresses=attempted_addresses(connection, release),
        )


@router.get(_DISPATCH)
def read_dispatch_history(
    run_id: int, version: int, _: AdminUser, before: Annotated[int, Query(gt=0)] | None = None
) -> list[DispatchRecord]:
    with get_engine().connect() as connection:
        return dispatch_history(connection, _release_id(connection, run_id, version), before)


def _queue(
    run_id: int, version: int, values: DispatchRequest, actor_id: int, retry_of: int | None = None
) -> DispatchRecord:
    with get_engine().begin() as connection:
        release = _release_id(connection, run_id, version, latest=True)
        try:
            return queue_dispatch(
                connection,
                release_id=release,
                version=version,
                request=values,
                actor_id=actor_id,
                retry_of=retry_of,
            )
        except DispatchError as error:
            raise HTTPException(error.status, detail=error.code) from None


@router.post(_DISPATCH, dependencies=_BROWSER, status_code=202)
def send_release(
    run_id: int, version: int, values: NewDispatch, actor: AdminUser
) -> DispatchRecord:
    return _queue(run_id, version, values, actor.id)


@router.post(_DISPATCH + "/{dispatch_id}/retry", dependencies=_BROWSER, status_code=202)
def retry_dispatch(
    run_id: int,
    version: int,
    dispatch_id: int,
    values: DispatchRequest,
    actor: AdminUser,
) -> DispatchRecord:
    return _queue(run_id, version, values, actor.id, retry_of=dispatch_id)
