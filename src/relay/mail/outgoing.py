import smtplib
import ssl
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import formatdate
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator, model_validator
from sqlalchemy import Connection, RowMapping, text

from relay.database.session import get_engine

SmtpHost = Literal["smtp.263.net", "smtpw.263.net", "smtp.163.com"]
NETEASE_HOST = "smtp.163.com"
_WORKBOOK_TYPE = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"

Address = Annotated[EmailStr, Field(max_length=254)]


class OutgoingInput(BaseModel):
    address: Address
    host: SmtpHost
    domains: list[str] = Field(max_length=20)
    test_recipient: Address | None = None
    password: SecretStr | None = None

    @field_validator("domains")
    @classmethod
    def clean_domains(cls, values: list[str]) -> list[str]:
        domains = []
        for value in values:
            domain = value.strip().lower()
            labels = domain.split(".")
            if (
                len(labels) < 2
                or len(domain) > 253
                or not domain.isascii()
                or any(
                    not label
                    or len(label) > 63
                    or not label[0].isalnum()
                    or not label[-1].isalnum()
                    or not all(char.isalnum() or char == "-" for char in label)
                    for label in labels
                )
            ):
                raise ValueError("Expected an email domain")
            domains.append(domain)
        return sorted(set(domains))

    @model_validator(mode="after")
    def check_provider(self) -> Self:
        is_netease = self.address.rsplit("@", 1)[-1].lower() == "163.com"
        if (
            is_netease != (self.host == NETEASE_HOST)
            or (is_netease and (not self.test_recipient or self.domains))
            or (not is_netease and (not self.domains or self.test_recipient))
        ):
            raise ValueError("Provider and recipient restrictions do not match")
        if not self.address.isascii() or (
            self.test_recipient and not self.test_recipient.isascii()
        ):
            raise ValueError("Expected ASCII mailbox address")
        if self.password is not None:
            password = self.password.get_secret_value()
            if (
                not password
                or len(password) > 512
                or not password.isascii()
                or not password.isprintable()
            ):
                raise ValueError("Invalid SMTP credential")
        return self


class OutgoingConfig(BaseModel):
    address: str
    host: SmtpHost
    domains: list[str]
    test_recipient: str | None
    revision: UUID
    verified: bool

    def allows(self, address: str) -> bool:
        if self.test_recipient:
            return address.lower() == self.test_recipient.lower()
        return address.rsplit("@", 1)[-1].lower() in self.domains


class OutgoingCredentials(OutgoingConfig):
    password: SecretStr


_PUBLIC = "address, host, domains, test_recipient, revision, verified"


def read_outgoing(connection: Connection, *, lock: bool = False) -> OutgoingConfig | None:
    row = (
        connection.execute(
            text(
                f"SELECT {_PUBLIC} FROM outgoing_mailbox WHERE id = 1"
                + (" FOR SHARE" if lock else "")
            )
        )
        .mappings()
        .one_or_none()
    )
    return OutgoingConfig.model_validate(row) if row is not None else None


def save_outgoing(values: OutgoingInput, cipher: Fernet) -> OutgoingConfig | None:
    params = {**values.model_dump(exclude={"password"}), "revision": uuid4()}
    with get_engine().begin() as connection:
        if values.password is None:
            query = (
                "UPDATE outgoing_mailbox SET domains = :domains, test_recipient = :test_recipient, "
                "revision = :revision, verified = false "
                "WHERE id = 1 AND address = :address AND host = :host"
            )
        else:
            params["encrypted"] = cipher.encrypt(values.password.get_secret_value().encode())
            query = """
                INSERT INTO outgoing_mailbox
                    (id, address, host, encrypted_password, domains, test_recipient, revision)
                VALUES (1, :address, :host, :encrypted, :domains, :test_recipient, :revision)
                ON CONFLICT (id) DO UPDATE SET address = EXCLUDED.address, host = EXCLUDED.host,
                    encrypted_password = EXCLUDED.encrypted_password, domains = EXCLUDED.domains,
                    test_recipient = EXCLUDED.test_recipient, revision = EXCLUDED.revision,
                    verified = false
            """
        row = (
            connection.execute(text(f"{query} RETURNING {_PUBLIC}"), params)
            .mappings()
            .one_or_none()
        )
    return OutgoingConfig.model_validate(row) if row is not None else None


def read_credentials() -> RowMapping | None:
    with get_engine().connect() as connection:
        return (
            connection.execute(
                text(f"SELECT {_PUBLIC}, encrypted_password FROM outgoing_mailbox WHERE id = 1")
            )
            .mappings()
            .one_or_none()
        )


def decrypt_credentials(row: RowMapping, cipher: Fernet) -> OutgoingCredentials:
    return OutgoingCredentials.model_validate(
        {**row, "password": cipher.decrypt(bytes(row.encrypted_password)).decode()}
    )


@contextmanager
def _smtp(credentials: OutgoingCredentials) -> Iterator[smtplib.SMTP_SSL]:
    client = smtplib.SMTP_SSL(
        credentials.host, 465, timeout=15, context=ssl.create_default_context()
    )
    try:
        client.login(credentials.address, credentials.password.get_secret_value())
        yield client
    finally:
        with suppress(OSError):
            client.close()


@dataclass(frozen=True)
class SendResult:
    state: Literal["accepted", "failed", "uncertain"]
    detail: str | None = None
    code: int | None = None


def check_outgoing(credentials: OutgoingCredentials) -> SendResult:
    try:
        with _smtp(credentials):
            return SendResult("accepted")
    except smtplib.SMTPAuthenticationError as error:
        return SendResult("failed", "smtp_auth_failed", error.smtp_code)
    except ssl.SSLError:
        return SendResult("failed", "smtp_tls_failed")
    except OSError:
        return SendResult("failed", "smtp_unreachable")


@dataclass(frozen=True)
class OutgoingMessage:
    address: str
    subject: str
    body: str
    filename: str
    workbook: bytes
    message_id: str


def send_workbook(credentials: OutgoingCredentials, outgoing: OutgoingMessage) -> SendResult:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = credentials.address
    message["To"] = outgoing.address
    message["Subject"] = outgoing.subject
    message["Message-ID"] = outgoing.message_id
    message["Date"] = formatdate(localtime=False)
    message.set_content(outgoing.body, cte="quoted-printable")
    message.add_attachment(
        outgoing.workbook,
        maintype="application",
        subtype=_WORKBOOK_TYPE,
        filename=outgoing.filename,
    )
    content = message.as_bytes()

    try:
        with _smtp(credentials) as client:
            code, _ = client.mail(credentials.address)
            if code != 250:
                return SendResult("failed", "smtp_sender_rejected", code)
            code, _ = client.rcpt(outgoing.address)
            if code not in (250, 251, 252):
                return SendResult("failed", "smtp_recipient_rejected", code)
            if client.sock is not None:
                client.sock.settimeout(90)
            try:
                code, _ = client.data(content)
            except smtplib.SMTPDataError as error:
                return SendResult("failed", "smtp_message_rejected", error.smtp_code)
            except OSError:
                return SendResult("uncertain", "smtp_acceptance_unknown")
            if code == 250:
                return SendResult("accepted", code=code)
            return SendResult("failed", "smtp_message_rejected", code)
    except smtplib.SMTPAuthenticationError as error:
        return SendResult("failed", "smtp_auth_failed", error.smtp_code)
    except ssl.SSLError:
        return SendResult("failed", "smtp_tls_failed")
    except OSError:
        return SendResult("failed", "smtp_unreachable")
