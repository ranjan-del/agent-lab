"""Ingestion against a real Postgres.

The unit tests prove the parser reads .ics correctly. Only this file can prove the thing that
actually matters: **running the import twice changes nothing.** That property lives in a unit
constraint the database enforces, so a fake database would test the fake, not the promise.

Skipped, not failed, when no database is reachable, so `uv run pytest` still works with the
containers down. CI always has one, so the skip can never hide a regression there.
"""

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import ics_fixtures
from agent_lab.ingest.runner import AlreadyIngested, ingest_events
from agent_lab.ingest.sources import ics
from agent_lab.models import Attendee, IngestionRun, Meeting, People

WINDOW_START = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)
WINDOW_END = dt.datetime(2026, 12, 31, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)


def _events(tmp_path: Path, body: str) -> list:
    path = tmp_path / "cal.ics"
    path.write_text(body)
    return list(ics.parse(path, WINDOW_START, WINDOW_END))


def _counts(session: Session) -> tuple[int, int, int]:
    return (
        session.execute(select(func.count()).select_from(Meeting)).scalar_one(),
        session.execute(select(func.count()).select_from(People)).scalar_one(),
        session.execute(select(func.count()).select_from(Attendee)).scalar_one(),
    )


def test_importing_the_same_calendar_twice_changes_nothing(
    session: Session, tmp_path: Path
) -> None:
    """The definition-of-done test for week 1.

    The second run is forced past the idempotency-key short circuit on purpose, so this
    exercises the database constraint rather than the skip.
    """
    events = _events(tmp_path, ics_fixtures.SERIES_WITH_EXCEPTIONS)

    ingest_events(session, events, source="ics", idempotency_key="k1", now=NOW)
    after_first = _counts(session)

    ingest_events(session, events, source="ics", idempotency_key="k1", now=NOW, force=True)
    after_second = _counts(session)

    assert after_first == after_second, f"{after_first} became {after_second}"
    assert after_first[0] == 3  # COUNT=4 minus one EXDATE


def test_the_idempotency_key_short_circuits_a_repeat(session: Session, tmp_path: Path) -> None:
    events = _events(tmp_path, ics_fixtures.SIMPLE)
    ingest_events(session, events, source="ics", idempotency_key="k2", now=NOW)

    with pytest.raises(AlreadyIngested):
        ingest_events(session, events, source="ics", idempotency_key="k2", now=NOW)


def test_a_failed_run_records_why(session: Session, tmp_path: Path) -> None:
    """Silence is the worst outcome of a nightly job. A failure must leave a row."""

    def exploding() -> Iterator:
        yield from _events(tmp_path, ics_fixtures.SIMPLE)
        raise RuntimeError("source went away")

    with pytest.raises(RuntimeError):
        ingest_events(session, exploding(), source="ics", idempotency_key="k3", now=NOW)

    run = session.execute(
        select(IngestionRun).where(IngestionRun.idempotency_key == "k3")
    ).scalar_one()
    assert run.status == "failed"
    assert "RuntimeError: source went away" in (run.error or "")


def test_a_room_is_stored_as_a_resource_not_a_person(session: Session, tmp_path: Path) -> None:
    """A room treated as a person triggers a spurious double-booking violation."""
    ingest_events(
        session, _events(tmp_path, ics_fixtures.SIMPLE), source="ics", idempotency_key="k4", now=NOW
    )

    room = session.execute(
        select(People).where(People.email == "room-1@resource.example.com")
    ).scalar_one()
    human = session.execute(select(People).where(People.email == "sriram@example.com")).scalar_one()
    assert room.is_resource
    assert not human.is_resource


def test_externality_is_derived_from_attendee_domains(session: Session, tmp_path: Path) -> None:
    """Mirrors a production lesson: this is a fact about who is in the room, so it is computed.

    The internal meeting also has a room on a different domain, which must NOT make it look
    external. That is precisely the false positive the resource flag exists to prevent.
    """
    ingest_events(
        session, _events(tmp_path, ics_fixtures.SIMPLE), source="ics", idempotency_key="k5", now=NOW
    )
    ingest_events(
        session,
        _events(tmp_path, ics_fixtures.EXTERNAL),
        source="ics",
        idempotency_key="k6",
        now=NOW,
    )

    internal = session.execute(select(Meeting).where(Meeting.title == "Design review")).scalar_one()
    external = session.execute(select(Meeting).where(Meeting.title == "Client call")).scalar_one()

    assert not internal.is_external
    assert external.is_external


def test_times_survive_the_round_trip_through_postgres(session: Session, tmp_path: Path) -> None:
    """A meeting written at 04:30 UTC must read back as 04:30 UTC, not shifted by the server."""
    ingest_events(
        session, _events(tmp_path, ics_fixtures.SIMPLE), source="ics", idempotency_key="k7", now=NOW
    )
    meeting = session.execute(select(Meeting).where(Meeting.title == "Design review")).scalar_one()

    assert meeting.starts_at.astimezone(dt.UTC) == dt.datetime(2026, 9, 1, 4, 30, tzinfo=dt.UTC)
    assert meeting.ends_at.astimezone(dt.UTC) == dt.datetime(2026, 9, 1, 5, 15, tzinfo=dt.UTC)


def test_a_person_on_many_occurrences_is_counted_as_created_once(
    session: Session, tmp_path: Path
) -> None:
    """A recurring meeting must not report the same attendee as created on every occurrence.

    The first implementation compared created_at to `now`, which is identical for every row in
    a single run, so one person on five occurrences reported as five people created. The fix
    reads Postgres's xmax, which is 0 only for a row the statement actually inserted.
    """
    events = _events(tmp_path, ics_fixtures.SERIES_WITH_EXCEPTIONS)
    assert len(events) == 3, "fixture should give three occurrences sharing one attendee"

    counts = ingest_events(session, events, source="ics", idempotency_key="k8", now=NOW)

    assert counts.people_created == 1
    assert counts.meetings_inserted == 3
    assert counts.meetings_updated == 0
    assert session.execute(select(func.count()).select_from(People)).scalar_one() == 1
