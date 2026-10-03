"""Tool 1: read the calendar for a window and say how loaded each day is.

Returns data, never prose: the meetings, the load per day, and the free slots inside working
hours. The model turns that into advice; this function only reports what the calendar holds.
Working hours are the ``working_hours`` policy row, passed in by the caller, never a constant
here, so the slots offered and the rule the gate enforces are the same numbers.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agent_lab.agent.policy.rules import DailyWindow
from agent_lab.models import Meeting

DEFAULT_TZ = "Asia/Kolkata"
MIN_SLOT = dt.timedelta(minutes=30)


class CalendarWindowArgs(BaseModel):
    """What the model must supply: an aware start and end. Everything else has a default."""

    model_config = ConfigDict(extra="forbid")

    start: dt.datetime
    end: dt.datetime
    timezone: str = DEFAULT_TZ

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None:
            raise ValueError("must be timezone-aware, e.g. 2026-09-08T14:30:00+05:30")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> CalendarWindowArgs:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


def read_calendar_window(
    session: Session, args: CalendarWindowArgs, *, working_hours: DailyWindow
) -> dict[str, Any]:
    tz = ZoneInfo(args.timezone)
    # Written with the range operator on purpose: that is the form the GiST span index
    # serves (docs/explain/README.md). The two-comparison form sequential-scans.
    overlap = text(
        "tstzrange(meetings.starts_at, meetings.ends_at, '[)') && tstzrange(:s, :e, '[)')"
    )
    rows = session.execute(
        select(Meeting)
        .where(overlap.bindparams(s=args.start, e=args.end), Meeting.status != "cancelled")
        .order_by(Meeting.starts_at)
    ).scalars()

    meetings: list[dict[str, Any]] = []
    load: dict[str, dict[str, int]] = {}
    by_day: dict[dt.date, list[tuple[dt.datetime, dt.datetime]]] = {}
    for m in rows:
        start, end = m.starts_at.astimezone(tz), m.ends_at.astimezone(tz)
        minutes = int((end - start).total_seconds() // 60)
        meetings.append(
            {
                "id": m.id,
                "title": m.title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "minutes": minutes,
            }
        )
        day = load.setdefault(start.date().isoformat(), {"meetings": 0, "minutes": 0})
        day["meetings"] += 1
        day["minutes"] += minutes
        by_day.setdefault(start.date(), []).append((start, end))

    return {
        "window": {
            "start": args.start.astimezone(tz).isoformat(),
            "end": args.end.astimezone(tz).isoformat(),
        },
        "meetings": meetings,
        "load_per_day": load,
        "free_slots": _free_slots(
            args.start.astimezone(tz), args.end.astimezone(tz), by_day, tz, working_hours
        ),
    }


def _free_slots(
    start: dt.datetime,
    end: dt.datetime,
    by_day: dict[dt.date, list[tuple[dt.datetime, dt.datetime]]],
    tz: ZoneInfo,
    working_hours: DailyWindow,
) -> list[dict[str, str]]:
    """Gaps of at least MIN_SLOT inside working hours, per local day in the window.

    Every day in the window is scanned, as before; the row's ``days`` are not applied here.
    """
    slots: list[dict[str, str]] = []
    day = start.date()
    while day < end.date():
        # The window is wall-clock time in the row's own zone, reported in the caller's.
        zone = working_hours.zone
        work_start = dt.datetime.combine(day, working_hours.start, tzinfo=zone).astimezone(tz)
        work_end = dt.datetime.combine(day, working_hours.end, tzinfo=zone).astimezone(tz)
        cursor = work_start
        for m_start, m_end in sorted(by_day.get(day, [])):
            if m_start - cursor >= MIN_SLOT:
                slots.append(
                    {"start": cursor.isoformat(), "end": min(m_start, work_end).isoformat()}
                )
            cursor = max(cursor, m_end)
        if work_end - cursor >= MIN_SLOT:
            slots.append({"start": cursor.isoformat(), "end": work_end.isoformat()})
        day += dt.timedelta(days=1)
    return slots
