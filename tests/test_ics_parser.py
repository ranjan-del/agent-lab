"""The .ics parser, with no database involved.

Each test pins one awkward property of real calendar data.
"""

import datetime as dt
from pathlib import Path

import pytest

import ics_fixtures
from agent_lab.ingest.sources import ics

WINDOW_START = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)
WINDOW_END = dt.datetime(2026, 12, 31, tzinfo=dt.UTC)


def _parse(tmp_path: Path, body: str) -> list:
    path = tmp_path / "cal.ics"
    path.write_text(body)
    return list(ics.parse(path, WINDOW_START, WINDOW_END))


def test_simple_event_normalises_times_attendees_and_rooms(tmp_path: Path) -> None:
    (event,) = _parse(tmp_path, ics_fixtures.SIMPLE)

    assert event.title == "Design review"
    assert event.starts_at == dt.datetime(2026, 9, 1, 4, 30, tzinfo=dt.UTC)
    assert event.ends_at == dt.datetime(2026, 9, 1, 5, 15, tzinfo=dt.UTC)
    assert event.series_id is None

    by_email = {a.email: a for a in event.attendees}
    assert by_email["ranjan@example.com"].is_organizer
    assert by_email["sriram@example.com"].response_status == "NEEDS-ACTION"
    # A room is not a person. Treating it as one causes a spurious double-booking violation.
    assert by_email["room-1@resource.example.com"].is_resource
    assert not by_email["sriram@example.com"].is_resource


def test_all_day_events_are_skipped(tmp_path: Path) -> None:
    """A date with no time cannot overlap a focus block, so it is not a meeting."""
    assert _parse(tmp_path, ics_fixtures.ALL_DAY) == []


def test_recurrence_expands_to_one_event_per_occurrence(tmp_path: Path) -> None:
    events = _parse(tmp_path, ics_fixtures.DST_WEEKLY)
    assert len(events) == 6
    assert all(e.series_id == "evt-standup" for e in events)
    # Every occurrence gets its own stable, unique id, which is what the unique constraint
    # keys on and therefore what makes re-import a no-op.
    assert len({e.external_id for e in events}) == 6


def test_recurrence_respects_dst_not_a_fixed_utc_offset(tmp_path: Path) -> None:
    """The standup stays at 10:00 New York time across the 1 Nov DST change.

    A naive expansion adds seven days in UTC and silently moves every later occurrence by an
    hour. This is the bug that only appears twice a year, in someone else's timezone.
    """
    events = sorted(_parse(tmp_path, ics_fixtures.DST_WEEKLY), key=lambda e: e.starts_at)
    utc_hours = [e.starts_at.hour for e in events]

    # 20, 27 Oct are EDT (UTC-4) -> 14:00 UTC. 3, 10, 17, 24 Nov are EST (UTC-5) -> 15:00 UTC.
    assert utc_hours == [14, 14, 15, 15, 15, 15], utc_hours


def test_exdate_removes_an_occurrence_and_recurrence_id_moves_one(tmp_path: Path) -> None:
    events = sorted(
        _parse(tmp_path, ics_fixtures.SERIES_WITH_EXCEPTIONS), key=lambda e: e.starts_at
    )
    starts = [e.starts_at for e in events]

    # COUNT=4 minus one EXDATE leaves three occurrences.
    assert len(events) == 3
    # The cancelled one is gone.
    assert dt.datetime(2026, 9, 14, 4, 30, tzinfo=dt.UTC) not in starts
    # The moved one appears once, at its NEW time, flagged as an exception.
    moved = [e for e in events if e.is_exception]
    assert len(moved) == 1
    assert moved[0].starts_at == dt.datetime(2026, 9, 21, 11, 30, tzinfo=dt.UTC)
    # And crucially NOT also at its original time, which would show the day twice.
    assert dt.datetime(2026, 9, 21, 4, 30, tzinfo=dt.UTC) not in starts


def test_window_bounds_the_expansion(tmp_path: Path) -> None:
    """An unbounded series must not expand forever. The window is the guard."""
    path = tmp_path / "cal.ics"
    path.write_text(ics_fixtures.DST_WEEKLY)
    narrow = list(
        ics.parse(
            path,
            dt.datetime(2026, 10, 19, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 2, tzinfo=dt.UTC),
        )
    )
    assert len(narrow) == 2  # 20 and 27 Oct; 3 Nov is outside the window


def test_a_naive_or_backwards_event_is_refused_loudly() -> None:
    """The dataclass rejects what the database would reject, but names the event."""
    from agent_lab.ingest.types import RawEvent

    with pytest.raises(ValueError, match="naive"):
        RawEvent(
            source="ics",
            external_id="bad-naive",
            starts_at=dt.datetime(2026, 9, 1, 10, 0),
            ends_at=dt.datetime(2026, 9, 1, 11, 0),
        )

    with pytest.raises(ValueError, match="not after"):
        RawEvent(
            source="ics",
            external_id="bad-order",
            starts_at=dt.datetime(2026, 9, 1, 11, 0, tzinfo=dt.UTC),
            ends_at=dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC),
        )
