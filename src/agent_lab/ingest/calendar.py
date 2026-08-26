"""Write RawEvents into the database, idempotently.

The whole design rests on one idea: **the database, not this code, guarantees that a second
import changes nothing.** Every write is an ``INSERT ... ON CONFLICT DO UPDATE`` keyed on the
same unique constraints the schema already declares. A bug in this file cannot produce
duplicates, because the constraint refuses them.

That distinction matters. Checking "does it exist?" before inserting is a race, and it also
stops protecting you the moment someone writes a second import path.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agent_lab.ingest.types import RawAttendee, RawEvent
from agent_lab.models import Attendee, Meeting, People

# Postgres sets xmax to 0 on a row this statement INSERTed, and to a live transaction id on
# a row it UPDATEd via ON CONFLICT. It is the only reliable way to tell the two apart from a
# single RETURNING clause. The obvious alternative, comparing a created_at timestamp, counts
# the same row as "created" again later in the same run, because the timestamp is identical.
_WAS_INSERTED = (literal_column("xmax") == literal_column("0")).label("inserted")


@dataclass(slots=True)
class IngestCounts:
    """What one import actually did, so the caller can print a number rather than a guess."""

    meetings_inserted: int = 0
    meetings_updated: int = 0
    people_created: int = 0
    attendees_written: int = 0

    @property
    def rows_written(self) -> int:
        return self.meetings_inserted + self.meetings_updated + self.attendees_written


def _upsert_person(session: Session, raw: RawAttendee, now: dt.datetime) -> tuple[int, bool]:
    """Return a person id, creating the row on first sight. The bool is True when created.

    Identity is the email, lowercased by the source. Two spellings of a display name are the
    same human; two emails are not, and merging those is a decision no importer should make.

    COALESCE on the name means a source that omits it cannot erase a name already known, while
    a name arriving for someone previously seen without one is still an improvement.
    """
    statement = insert(People).values(
        email=raw.email,
        display_name=raw.display_name,
        timezone="Asia/Kolkata",
        focus_hours={},
        is_resource=raw.is_resource,
        is_trainee=False,
        created_at=now,
    )
    upsert = statement.on_conflict_do_update(
        index_elements=["email"],
        set_={
            "display_name": func.coalesce(statement.excluded.display_name, People.display_name),
            "is_resource": statement.excluded.is_resource,
        },
    ).returning(People.id, _WAS_INSERTED)

    person_id, inserted = session.execute(upsert).one()
    return int(person_id), bool(inserted)


def upsert_event(session: Session, event: RawEvent, now: dt.datetime) -> IngestCounts:
    """Write one occurrence and its attendees. Safe to call repeatedly with the same input."""
    counts = IngestCounts()

    organizer_id: int | None = None
    person_ids: dict[str, int] = {}
    for raw_attendee in event.attendees:
        person_id, created = _upsert_person(session, raw_attendee, now)
        person_ids[raw_attendee.email] = person_id
        if created:
            counts.people_created += 1
        if raw_attendee.email == event.organizer_email:
            organizer_id = person_id

    is_external = _is_external(event)

    meeting_stmt = (
        insert(Meeting)
        .values(
            source=event.source,
            external_id=event.external_id,
            series_id=event.series_id,
            is_exception=event.is_exception,
            title=event.title,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            organizer_id=organizer_id,
            is_external=is_external,
            status=event.status,
            raw=event.raw,
            ingested_at=now,
        )
        # This is the line that makes re-running the import a no-op. The constraint is
        # `uq_meetings_source_external_id`, declared in the schema, not here.
        .on_conflict_do_update(
            index_elements=["source", "external_id"],
            set_={
                "series_id": event.series_id,
                "is_exception": event.is_exception,
                "title": event.title,
                "starts_at": event.starts_at,
                "ends_at": event.ends_at,
                "organizer_id": organizer_id,
                "is_external": is_external,
                "status": event.status,
                "raw": event.raw,
                "ingested_at": now,
            },
        )
        .returning(Meeting.id, _WAS_INSERTED)
    )
    meeting_id, meeting_inserted = session.execute(meeting_stmt).one()
    if meeting_inserted:
        counts.meetings_inserted += 1
    else:
        counts.meetings_updated += 1

    for raw_attendee in event.attendees:
        session.execute(
            insert(Attendee)
            .values(
                meeting_id=meeting_id,
                person_id=person_ids[raw_attendee.email],
                response_status=raw_attendee.response_status,
                is_optional=raw_attendee.is_optional,
                is_organizer=raw_attendee.is_organizer,
            )
            .on_conflict_do_update(
                index_elements=["meeting_id", "person_id"],
                set_={
                    "response_status": raw_attendee.response_status,
                    "is_optional": raw_attendee.is_optional,
                    "is_organizer": raw_attendee.is_organizer,
                },
            )
        )
        counts.attendees_written += 1

    return counts


def _is_external(event: RawEvent) -> bool:
    """Derive externality from the attendee domains, never from the source's own claim.

    This mirrors a production lesson: a model asked to decide whether a meeting was external
    got it wrong in both directions. It is a fact about who is in the room, so it is computed
    from who is in the room. Resources are excluded because a room has no organisation.
    """
    if event.organizer_email is None:
        return False
    home = event.organizer_email.split("@")[-1]
    return any(not a.is_resource and a.email.split("@")[-1] != home for a in event.attendees)
