"""Tool 1: read_calendar_window. Reads meetings, returns load per day and free slots.

Against a real Postgres: the overlap query is the reason the span index exists.
"""

import datetime as dt

from sqlalchemy.orm import Session

from agent_lab.agent.policy.rules import DailyWindow, load_policies, params_of
from agent_lab.agent.tools_calendar import CalendarWindowArgs, read_calendar_window
from agent_lab.models import Meeting

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _meeting(session: Session, title: str, start: dt.datetime, minutes: int) -> Meeting:
    m = Meeting(
        source="test",
        external_id=f"{title}-{start.isoformat()}",
        title=title,
        starts_at=start,
        ends_at=start + dt.timedelta(minutes=minutes),
        status="confirmed",
        raw={},
        ingested_at=dt.datetime.now(dt.UTC),
    )
    session.add(m)
    return m


def _working_hours(session: Session) -> DailyWindow:
    """The seeded ``working_hours`` row, read from the database the way execute() reads it."""
    return params_of(load_policies(session), "working_hours", DailyWindow)


def test_load_per_day_and_free_slots_inside_working_hours(session: Session) -> None:
    tue = dt.datetime(2026, 9, 8, tzinfo=IST)
    wed = dt.datetime(2026, 9, 9, tzinfo=IST)
    _meeting(session, "Standup", tue.replace(hour=11), 30)  # 11:00-11:30
    _meeting(session, "Client demo", tue.replace(hour=14), 60)  # 14:00-15:00
    _meeting(session, "One-to-one", wed.replace(hour=16), 60)  # 16:00-17:00
    session.flush()

    result = read_calendar_window(
        session,
        CalendarWindowArgs(start=tue, end=wed + dt.timedelta(days=1)),
        working_hours=_working_hours(session),
    )

    # per-day load, keyed by local date, counts and minutes
    assert result["load_per_day"] == {
        "2026-09-08": {"meetings": 2, "minutes": 90},
        "2026-09-09": {"meetings": 1, "minutes": 60},
    }
    # free slots inside working hours 10:00-19:00 IST, at least 30 minutes long
    tue_free = [
        (s["start"], s["end"]) for s in result["free_slots"] if s["start"].startswith("2026-09-08")
    ]
    assert tue_free == [
        ("2026-09-08T10:00:00+05:30", "2026-09-08T11:00:00+05:30"),
        ("2026-09-08T11:30:00+05:30", "2026-09-08T14:00:00+05:30"),
        ("2026-09-08T15:00:00+05:30", "2026-09-08T19:00:00+05:30"),
    ]
    assert [m["title"] for m in result["meetings"]] == ["Standup", "Client demo", "One-to-one"]


def test_cancelled_meetings_do_not_count_toward_load(session: Session) -> None:
    tue = dt.datetime(2026, 9, 8, tzinfo=IST)
    live = _meeting(session, "Standup", tue.replace(hour=11), 30)
    dead = _meeting(session, "Cancelled sync", tue.replace(hour=12), 60)
    dead.status = "cancelled"
    session.flush()

    result = read_calendar_window(
        session,
        CalendarWindowArgs(start=tue, end=tue + dt.timedelta(days=1)),
        working_hours=_working_hours(session),
    )

    assert result["load_per_day"] == {"2026-09-08": {"meetings": 1, "minutes": 30}}
    assert [m["id"] for m in result["meetings"]] == [live.id]


def test_a_meeting_straddling_the_window_edge_is_included(session: Session) -> None:
    """Overlap, not containment: a meeting that starts before the window and ends inside it
    still occupies time inside the window."""
    tue = dt.datetime(2026, 9, 8, tzinfo=IST)
    _meeting(session, "Early call", tue.replace(hour=9, minute=30), 60)  # 09:30-10:30
    session.flush()

    result = read_calendar_window(
        session,
        CalendarWindowArgs(start=tue.replace(hour=10), end=tue.replace(hour=18)),
        working_hours=_working_hours(session),
    )

    assert [m["title"] for m in result["meetings"]] == ["Early call"]


def test_naive_or_backwards_windows_are_refused_with_a_message_the_model_can_fix() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="timezone-aware"):
        CalendarWindowArgs(start=dt.datetime(2026, 9, 8, 10), end=dt.datetime(2026, 9, 8, 18))
    with pytest.raises(ValidationError, match="end must be after start"):
        CalendarWindowArgs(
            start=dt.datetime(2026, 9, 8, 18, tzinfo=IST),
            end=dt.datetime(2026, 9, 8, 10, tzinfo=IST),
        )


def test_free_slots_follow_the_working_hours_row_not_a_constant(session: Session) -> None:
    """A data edit to ``working_hours`` moves the free slots with no code change."""
    tue = dt.datetime(2026, 9, 8, tzinfo=IST)
    _meeting(session, "Standup", tue.replace(hour=11), 30)
    session.flush()
    early = DailyWindow.model_validate(
        {"start": "08:00", "end": "12:00", "days": [1, 2, 3, 4, 5], "tz": "Asia/Kolkata"}
    )

    result = read_calendar_window(
        session,
        CalendarWindowArgs(start=tue, end=tue + dt.timedelta(days=1)),
        working_hours=early,
    )

    assert [(s["start"], s["end"]) for s in result["free_slots"]] == [
        ("2026-09-08T08:00:00+05:30", "2026-09-08T11:00:00+05:30"),
        ("2026-09-08T11:30:00+05:30", "2026-09-08T12:00:00+05:30"),
    ]
