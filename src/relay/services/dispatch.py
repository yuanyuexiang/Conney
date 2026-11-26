import json
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Connection, text

from relay.mail.outgoing import Address, OutgoingConfig, read_outgoing

State = Literal["queued", "sending", "accepted", "failed", "uncertain"]


class GroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    addresses: list[Address] = Field(min_length=1, max_length=50)
    active: bool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.isprintable():
            raise ValueError("Invalid group name")
        return value

    @field_validator("addresses")
    @classmethod
    def clean_addresses(cls, values: list[str]) -> list[str]:
        if any(not value.isascii() for value in values):
            raise ValueError("Expected ASCII mailbox address")
        return sorted({value.lower() for value in values})


class RecipientGroup(GroupInput):
    id: int
    revision: int


class GroupEdit(GroupInput):
    revision: int = Field(ge=1)


class DispatchRecipient(BaseModel):
    id: int
    address: str
    departments: list[str]
    message_id: str
    state: State
    detail: str | None
    smtp_code: int | None
    finished_at: datetime | None
    retryable: bool


class DispatchRecord(BaseModel):
    id: int
    release_id: int
    sender: str
    subject: str
    body: str
    retry_of: int | None
    created_at: datetime
    created_by: str
    recipients: list[DispatchRecipient]


class DispatchRequest(BaseModel):
    request_id: UUID
    mailbox_revision: UUID
    addresses: list[Address] = Field(min_length=1, max_length=150)


class NewDispatch(DispatchRequest):
    group_ids: list[int] = Field(min_length=1, max_length=25)
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("subject")
    @classmethod
    def check_subject(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.isprintable():
            raise ValueError("Invalid subject")
        return value

    @field_validator("body")
    @classmethod
    def check_body(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or not value.replace("\n", "").replace("\r", "").replace("\t", "").isprintable()
        ):
            raise ValueError("Invalid body")
        return value


class DispatchError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _records(
    connection: Connection, where: str, params: dict[str, object], *, limit: int
) -> list[DispatchRecord]:
    rows = connection.execute(
        text(f"""
            SELECT d.id, d.release_id, d.sender, d.subject, d.body, d.retry_of, d.created_at,
                u.username AS created_by,
                (SELECT jsonb_agg(to_jsonb(rec) || jsonb_build_object('retryable',
                    rec.state = 'failed' AND NOT EXISTS (
                        SELECT 1 FROM delivery_dispatch_recipients AS newer
                        WHERE newer.release_id = rec.release_id AND newer.address = rec.address
                            AND newer.id > rec.id
                    )) ORDER BY rec.id)
                 FROM delivery_dispatch_recipients AS rec WHERE rec.dispatch_id = d.id)
                 AS recipients
            FROM delivery_dispatches AS d JOIN relay_users AS u ON u.id = d.created_by
            WHERE {where} ORDER BY d.id DESC LIMIT :limit
        """),
        {**params, "limit": limit},
    ).mappings()
    return [DispatchRecord.model_validate(row) for row in rows]


def dispatch_history(
    connection: Connection, release_id: int, before: int | None = None
) -> list[DispatchRecord]:
    return _records(
        connection,
        "d.release_id = :release AND (CAST(:before AS bigint) IS NULL OR d.id < :before)",
        {"release": release_id, "before": before},
        limit=25,
    )


def _record(connection: Connection, dispatch_id: int) -> DispatchRecord:
    rows = _records(connection, "d.id = :id", {"id": dispatch_id}, limit=1)
    if not rows:
        raise DispatchError("dispatch_not_found", 404)
    return rows[0]


def read_groups(connection: Connection, *, active_only: bool = False) -> list[RecipientGroup]:
    query = "SELECT * FROM delivery_recipient_groups"
    rows = connection.execute(
        text(query + (" WHERE active" if active_only else "") + " ORDER BY name, id")
    ).mappings()
    return [RecipientGroup.model_validate(row) for row in rows]


def eligible_groups(connection: Connection, config: OutgoingConfig) -> list[RecipientGroup]:
    return [
        group
        for group in read_groups(connection, active_only=True)
        if all(config.allows(address) for address in group.addresses)
    ]


def attempted_addresses(connection: Connection, release_id: int) -> list[str]:
    return list(
        connection.execute(
            text("""
        SELECT DISTINCT address FROM delivery_dispatch_recipients WHERE release_id = :id
    """),
            {"id": release_id},
        ).scalars()
    )


def _retry_addresses(connection: Connection, original: DispatchRecord) -> dict[str, list[str]]:
    # A retry can only originate from the most recent failed attempt for each address.
    return {
        row.address: row.departments
        for row in connection.execute(
            text("""
            SELECT old.address, old.departments FROM delivery_dispatch_recipients AS old
            WHERE old.dispatch_id = :id AND old.state = 'failed' AND NOT EXISTS (
                SELECT 1 FROM delivery_dispatch_recipients AS newer
                WHERE newer.release_id = old.release_id AND newer.address = old.address
                    AND newer.id > old.id
            )
        """),
            {"id": original.id},
        )
    }


def queue_dispatch(
    connection: Connection,
    *,
    release_id: int,
    version: int,
    request: DispatchRequest,
    actor_id: int,
    retry_of: int | None = None,
) -> DispatchRecord:
    request_payload = {
        "release_id": release_id,
        "retry_of": retry_of,
        **request.model_dump(mode="json"),
    }
    fingerprint = sha256(json.dumps(request_payload, sort_keys=True).encode()).hexdigest()
    # Serialize an identical request key even if two tabs target different releases by mistake.
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:request_id, 14014))"),
        {"request_id": str(request.request_id)},
    )
    existing = connection.execute(
        text("SELECT id, fingerprint FROM delivery_dispatches WHERE request_id = :request_id"),
        {"request_id": request.request_id},
    ).one_or_none()
    if existing:
        if existing.fingerprint != fingerprint:
            raise DispatchError("dispatch_request_conflict")
        return _record(connection, existing.id)

    config = read_outgoing(connection, lock=True)
    if config is None or not config.verified:
        raise DispatchError("outgoing_not_verified")
    if config.revision != request.mailbox_revision:
        raise DispatchError("outgoing_changed")
    recipients: dict[str, list[str]]
    if retry_of is not None:
        original = _record(connection, retry_of)
        if original.release_id != release_id:
            raise DispatchError("dispatch_not_found", 404)
        if original.sender != config.address:
            raise DispatchError("outgoing_sender_changed")
        recipients = _retry_addresses(connection, original)
        subject, body = original.subject, original.body
    else:
        assert isinstance(request, NewDispatch)
        selected = set(request.group_ids)
        groups = [group for group in eligible_groups(connection, config) if group.id in selected]
        if len(groups) != len(selected):
            raise DispatchError("dispatch_groups_changed")
        attempted = set(attempted_addresses(connection, release_id))
        recipients = {}
        for group in groups:
            for address in group.addresses:
                if address not in attempted:
                    recipients.setdefault(address, []).append(group.name)
        subject, body = request.subject, request.body
        if version > 1:
            body = f"本邮件附件 V{version} 替代 V{version - 1}，请以本版为准。\n\n{body}"

    if not recipients:
        raise DispatchError("dispatch_no_recipients")
    if set(request.addresses) != recipients.keys() or any(
        not config.allows(address) for address in recipients
    ):
        raise DispatchError("dispatch_recipients_changed")
    dispatch_id = connection.execute(
        text("""
            INSERT INTO delivery_dispatches (
                release_id, request_id, fingerprint, mailbox_revision, sender,
                subject, body, created_by, retry_of
            ) VALUES (
                :release, :request, :fingerprint, :mailbox_revision, :sender,
                :subject, :body, :actor, :retry
            ) RETURNING id
        """),
        {
            "release": release_id,
            "request": request.request_id,
            "fingerprint": fingerprint,
            "mailbox_revision": config.revision,
            "sender": config.address,
            "subject": subject,
            "body": body,
            "actor": actor_id,
            "retry": retry_of,
        },
    ).scalar_one()
    sender_domain = config.address.rsplit("@", 1)[-1]
    connection.execute(
        text("""
            INSERT INTO delivery_dispatch_recipients
                (dispatch_id, release_id, address, departments, message_id)
            VALUES (:dispatch, :release, :address, :departments, :message_id)
        """),
        [
            {
                "dispatch": dispatch_id,
                "release": release_id,
                "address": address,
                "departments": departments,
                "message_id": f"<relay-{uuid4().hex}@{sender_domain}>",
            }
            for address, departments in recipients.items()
        ],
    )
    return _record(connection, dispatch_id)
