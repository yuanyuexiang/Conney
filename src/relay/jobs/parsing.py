from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from sqlalchemy import text

from relay.database.session import get_engine
from relay.parsers.document import ParsedDocument

_MAX_ATTEMPTS = 4
_LEASE_SECONDS = 90
_LEASE_PREDICATE = """
    id = :id AND state = 'processing' AND lease_token = :token
    AND lease_until > clock_timestamp()
"""


@dataclass(frozen=True)
class ParseLease:
    id: int
    token: UUID
    sha256: str
    size_bytes: int
    attempts: int

    def key(self) -> dict[str, int | UUID]:
        return {"id": self.id, "token": self.token}


def claim() -> ParseLease | None:
    token = uuid4()
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "UPDATE parse_jobs SET state = 'failed', lease_token = NULL, lease_until = NULL, "
                "error_code = 'lease_expired' WHERE state = 'processing' "
                "AND lease_until <= clock_timestamp() AND attempts >= :limit"
            ),
            {"limit": _MAX_ATTEMPTS},
        )
        row = connection.execute(
            text(
                "WITH candidate AS (SELECT id FROM parse_jobs WHERE attempts < :limit AND "
                "((state = 'pending' AND available_at <= clock_timestamp()) OR "
                "(state = 'processing' AND lease_until <= clock_timestamp())) "
                "ORDER BY available_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "UPDATE parse_jobs AS job SET state = 'processing', attempts = job.attempts + 1, "
                "lease_token = :token, "
                "lease_until = clock_timestamp() + make_interval(secs => :lease) "
                "FROM candidate, received_attachments AS att, archive_objects AS blob "
                "WHERE job.id = candidate.id AND att.id = job.attachment_id "
                "AND blob.sha256 = att.sha256 "
                "RETURNING job.id, job.attempts, blob.sha256, blob.size_bytes"
            ),
            {"limit": _MAX_ATTEMPTS, "token": token, "lease": _LEASE_SECONDS},
        ).one_or_none()
    if row is None:
        return None
    return ParseLease(row.id, token, row.sha256, row.size_bytes, row.attempts)


def renew(lease: ParseLease) -> bool:
    with get_engine().begin() as connection:
        updated = connection.execute(
            text(
                "UPDATE parse_jobs SET lease_until = "
                "clock_timestamp() + make_interval(secs => :lease) "
                f"WHERE {_LEASE_PREDICATE} RETURNING id"
            ),
            {**lease.key(), "lease": _LEASE_SECONDS},
        ).scalar_one_or_none()
    return updated is not None


def fail(lease: ParseLease, code: str, *, retryable: bool) -> bool:
    retry = retryable and lease.attempts < _MAX_ATTEMPTS
    with get_engine().begin() as connection:
        updated = connection.execute(
            text(
                "UPDATE parse_jobs SET state = :state, lease_token = NULL, lease_until = NULL, "
                "available_at = clock_timestamp() + make_interval(secs => :delay), "
                "error_code = :code "
                f"WHERE {_LEASE_PREDICATE} RETURNING id"
            ),
            {
                **lease.key(),
                "state": "pending" if retry else "failed",
                "delay": 10 * 4 ** (lease.attempts - 1) if retry else 0,
                "code": code,
            },
        ).scalar_one_or_none()
    return updated is not None


def complete(lease: ParseLease, document: ParsedDocument) -> bool:
    # Mark success and store the complete immutable result in one transaction.
    with get_engine().begin() as connection:
        owned = connection.execute(
            text(
                "UPDATE parse_jobs SET state = 'succeeded', error_code = NULL, lease_token = NULL, "
                f"lease_until = NULL WHERE {_LEASE_PREDICATE} RETURNING id"
            ),
            lease.key(),
        ).scalar_one_or_none()
        if owned is None:
            return False
        run_id = connection.execute(
            text(
                "INSERT INTO parse_runs (job_id, template, parser_version, source_fields) "
                "VALUES (:job_id, :template, '1', :source) RETURNING id"
            ),
            {
                "job_id": lease.id,
                "template": document.template,
                "source": Jsonb({key: asdict(value) for key, value in document.fields.items()}),
            },
        ).scalar_one()
        for position, block in enumerate(document.blocks, start=1):
            block_id = connection.execute(
                text(
                    "INSERT INTO parsed_material_blocks (run_id, position, source_fields) "
                    "VALUES (:run_id, :position, :source) RETURNING id"
                ),
                {
                    "run_id": run_id,
                    "position": position,
                    "source": Jsonb({key: asdict(value) for key, value in block.fields.items()}),
                },
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO parsed_quantity_records (block_id, position, quantity, "
                    "requested_date, earliest_date, latest_date, source_fields) VALUES "
                    "(:block_id, :position, :quantity, :requested, :earliest, :latest, :source)"
                ),
                [
                    {
                        "block_id": block_id,
                        "position": row_number,
                        "quantity": record.quantity,
                        "requested": record.requested_date,
                        "earliest": record.earliest_date,
                        "latest": record.latest_date,
                        "source": Jsonb(
                            {key: asdict(value) for key, value in record.source.items()}
                        ),
                    }
                    for row_number, record in enumerate(block.quantities, start=1)
                ],
            )
    return True
