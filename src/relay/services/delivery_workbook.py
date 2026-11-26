from datetime import date

from relay.services.delivery import DraftSnapshot
from relay.services.delivery_xlsx import build_workbook
from relay.services.schedule_xlsx import build_schedule_workbook


def delivery_workbook(
    draft: DraftSnapshot, *, release_version: int | None = None, ship_through: date | None = None
) -> bytes:
    if draft.template == "drx_delfor":
        return build_workbook(draft, release_version=release_version, ship_through=ship_through)
    return build_schedule_workbook(draft, release_version=release_version)
