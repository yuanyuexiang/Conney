import logging
from time import monotonic
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from relay.database.session import get_engine

WorkerName = Literal["mail", "parser", "delivery"]
HEARTBEAT_STALE_SECONDS = 75
_REPORT_INTERVAL = 15
_LOG = logging.getLogger(__name__)


class WorkerHeartbeat:
    def __init__(self, worker: WorkerName) -> None:
        self.worker = worker
        self._next_report = 0.0

    def __call__(self) -> None:
        now = monotonic()
        if now < self._next_report:
            return
        self._next_report = now + _REPORT_INTERVAL
        try:
            with get_engine().begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO worker_heartbeats (worker, seen_at) "
                        "VALUES (:worker, clock_timestamp()) ON CONFLICT (worker) DO UPDATE "
                        "SET seen_at = GREATEST(worker_heartbeats.seen_at, EXCLUDED.seen_at)"
                    ),
                    {"worker": self.worker},
                )
        except SQLAlchemyError:
            _LOG.warning("%s worker heartbeat could not be recorded", self.worker)
