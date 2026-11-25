from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from relay.api.auth import SignedInUser
from relay.api.delivery import DRAFT_REVIEW_PREDICATE
from relay.api.intake import MailSummary, latest_mail
from relay.database.session import get_engine

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DAYS = 7


class DailyReceipts(BaseModel):
    date: date
    received: int


class DashboardSummary(BaseModel):
    checked_at: datetime
    receipts: list[DailyReceipts]
    parsed_today: int
    delivery_drafts: int
    drafts_for_review: int
    recent_mail: list[MailSummary]


@router.get("")
def read_dashboard(_: SignedInUser) -> DashboardSummary:
    checked_at = datetime.now(UTC)
    today = checked_at.astimezone(_SHANGHAI).date()
    first_day = today - timedelta(days=_DAYS - 1)
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '4s'"))
            receipts = {
                row.day: row.received
                for row in connection.execute(
                    text(
                        "SELECT (received_at AT TIME ZONE 'Asia/Shanghai')::date AS day, "
                        "count(*) AS received FROM received_mail "
                        "WHERE received_at >= :start AND received_at < :checked_at GROUP BY 1"
                    ),
                    {
                        "start": datetime.combine(first_day, time.min, _SHANGHAI),
                        "checked_at": checked_at,
                    },
                )
            }
            parsed_today = connection.execute(
                text(
                    "SELECT count(*) FROM parse_runs "
                    "WHERE created_at >= :start AND created_at < :checked_at"
                ),
                {
                    "start": datetime.combine(today, time.min, _SHANGHAI),
                    "checked_at": checked_at,
                },
            ).scalar_one()
            drafts = connection.execute(
                text(
                    "SELECT count(*) AS total, "
                    f"count(*) FILTER (WHERE {DRAFT_REVIEW_PREDICATE}) AS for_review "
                    "FROM delivery_drafts"
                )
            ).one()
            recent = latest_mail(connection, 5)
    except SQLAlchemyError:
        raise HTTPException(503, detail="dashboard_unavailable") from None

    days = [first_day + timedelta(days=offset) for offset in range(_DAYS)]
    return DashboardSummary(
        checked_at=checked_at,
        receipts=[DailyReceipts(date=day, received=receipts.get(day, 0)) for day in days],
        parsed_today=parsed_today,
        delivery_drafts=drafts.total,
        drafts_for_review=drafts.for_review,
        recent_mail=recent,
    )
