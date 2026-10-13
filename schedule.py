"""Once-a-day refresh slot at 06:00 Europe/Stockholm.

GitHub's scheduler speaks UTC, and Sweden switches between UTC+1 and UTC+2.
The slot itself is calculated in Swedish local time so the pull stays at
06:00 through the year.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from database import init_db, last_successful_run, lead_count, newest_lead_created_on

STOCKHOLM = ZoneInfo("Europe/Stockholm")
DAILY_HOUR = 6


def next_slot(now: datetime | None = None) -> datetime:
    """Next 06:00 Europe/Stockholm, aware of daylight saving."""
    current = _stockholm_now(now)
    slot = current.replace(hour=DAILY_HOUR, minute=0, second=0, microsecond=0)
    if current >= slot:
        slot += timedelta(days=1)
    return slot


def scheduled_run_is_due(now: datetime | None = None) -> bool:
    """True when this Swedish morning's pull has not been stored yet."""
    init_db()
    if lead_count() == 0:
        return True
    current = _stockholm_now(now)
    slot = current.replace(hour=DAILY_HOUR, minute=0, second=0, microsecond=0)
    if current < slot:
        slot -= timedelta(days=1)
    last = last_successful_run() or newest_lead_created_on()
    if last is None:
        return True
    last_utc = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last.astimezone(timezone.utc)
    return last_utc < slot.astimezone(timezone.utc)


def _stockholm_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(STOCKHOLM)
    if now.tzinfo is None:
        return now.replace(tzinfo=STOCKHOLM)
    return now.astimezone(STOCKHOLM)
