"""Read an .ics export into expanded occurrences.

An .ics file is the fastest path to real, messy calendar data: Google Calendar exports one in
about thirty seconds and it needs no OAuth. It is also genuinely awkward in all the ways a live
API is, which is the point.

The three awkward parts, all handled here:

* **Recurrence.** A weekly standup is one VEVENT with an RRULE. The schema stores expanded
  occurrences, so the rule is expanded against a bounded window. Unbounded expansion of an
  infinite series is how this code would hang.
* **Exceptions.** An occurrence that was moved or edited appears as a *separate* VEVENT
  carrying RECURRENCE-ID. It must replace the generated occurrence for that instant, not sit
  alongside it, or the day shows the meeting twice.
* **Cancellations.** EXDATE lists instants the series does not actually occur on.

All-day events are deliberately skipped: they carry a date with no time and no timezone, so
they cannot answer "does this overlap a focus block", which is the only question the agent
asks of a calendar.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dateutil.rrule import rrulestr
from icalendar import Calendar

from agent_lab.ingest.types import RawAttendee, RawEvent

SOURCE = "ics"

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Dropped:
    """What ``parse`` refused to turn into an event, counted by reason.

    Every refusal below is deliberate and stays deliberate. What changed is that the refusal
    is now counted, because a parser that loses rows without a number poisons every count
    built on top of it: the ingest output, and from week 4 the eval harness. Window exclusion
    is not counted here; the window is the caller's question, not lost data.

    Pass one in and read it after the iterator is exhausted. The generator stays lazy.
    """

    no_uid: int = 0
    all_day_or_floating: int = 0
    no_end: int = 0
    zero_length: int = 0

    @property
    def total(self) -> int:
        return self.no_uid + self.all_day_or_floating + self.no_end + self.zero_length

    def note(self, reason: str, uid: str) -> None:
        """Count one refusal and say so. The log line names the event; the count names the scale."""
        setattr(self, reason, getattr(self, reason) + 1)
        log.warning("ics: dropped %s (%s)", uid or "<no uid>", reason)


# Google marks rooms and equipment with CUTYPE. Anything that is not an individual is a
# resource, and a resource must never be treated as a person by the policy engine.
_RESOURCE_CUTYPES = frozenset({"RESOURCE", "ROOM"})


def _as_aware(value: Any) -> dt.datetime | None:
    """Return an aware datetime in its ORIGINAL timezone.

    Recurrence must be expanded in the timezone the series was written in. "Every Tuesday at
    10:00 New York time" is a statement about local time, and local time is not a fixed offset
    from UTC: it shifts at a DST boundary. Expanding in UTC adds a fixed seven days and
    silently moves every occurrence after the boundary by an hour.
    """
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return None
        return value
    return None


def _as_aware_utc(value: Any) -> dt.datetime | None:
    """Return an aware UTC datetime, or None for anything without a time of day.

    A date with no time (an all-day event) is not a moment, and pretending it is midnight
    somewhere invents information the source never contained.
    """
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            # A floating time means "whatever local time is where this is read", which is not
            # a fact. Refusing it here is better than silently guessing a timezone.
            return None
        return value.astimezone(dt.UTC)
    return None


def _occurrence_id(uid: str, starts_at: dt.datetime) -> str:
    """Build an id that is stable across re-imports and unique per occurrence.

    Stability is the whole point: run the import twice and the same occurrence must produce the
    same id, so the unique constraint turns the second run into a no-op.
    """
    return f"{uid}_{starts_at.astimezone(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _email(value: Any) -> str | None:
    """Pull a bare address out of a MAILTO: value."""
    if value is None:
        return None
    text = str(value).strip()
    if text.upper().startswith("MAILTO:"):
        text = text[7:]
    return text.lower() or None


def _attendees(component: Any) -> tuple[RawAttendee, ...]:
    """Collect attendees, marking rooms as resources and preserving RSVP state."""
    organizer = _email(component.get("ORGANIZER"))
    raw = component.get("ATTENDEE")
    if raw is None:
        entries: list[Any] = []
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = [raw]

    out: list[RawAttendee] = []
    seen: set[str] = set()
    for entry in entries:
        email = _email(entry)
        if email is None or email in seen:
            continue
        seen.add(email)
        params = getattr(entry, "params", {})
        cutype = str(params.get("CUTYPE", "INDIVIDUAL")).upper()
        out.append(
            RawAttendee(
                email=email,
                display_name=str(params["CN"]) if "CN" in params else None,
                response_status=str(params.get("PARTSTAT", "needsAction")),
                is_optional=str(params.get("ROLE", "")).upper() == "OPT-PARTICIPANT",
                is_organizer=email == organizer,
                is_resource=cutype in _RESOURCE_CUTYPES,
            )
        )
    if organizer and organizer not in seen:
        out.append(RawAttendee(email=organizer, is_organizer=True))
    return tuple(out)


def _raw_payload(component: Any) -> dict[str, Any]:
    """Keep the original fields, so a parser bug is re-parsed rather than re-fetched."""
    keep = ("SUMMARY", "LOCATION", "STATUS", "UID", "RRULE", "DESCRIPTION", "SEQUENCE")
    return {k: str(component.get(k)) for k in keep if component.get(k) is not None}


def parse(
    path: Path,
    window_start: dt.datetime,
    window_end: dt.datetime,
    *,
    dropped: Dropped | None = None,
) -> Iterator[RawEvent]:
    """Yield one RawEvent per occurrence falling inside the window.

    The window is required rather than optional. An .ics file can contain a series with no end
    date, and expanding one of those without a bound does not terminate.

    ``dropped``, when given, is filled with a count of every row refused and why. Without it
    the refusals are still logged, but nobody is counting.
    """
    counter = dropped if dropped is not None else Dropped()
    calendar = Calendar.from_ical(path.read_bytes())

    masters: list[Any] = []
    overrides: dict[tuple[str, dt.datetime], Any] = {}

    for component in calendar.walk("VEVENT"):
        uid = str(component.get("UID", "")).strip()
        if not uid:
            counter.note("no_uid", uid)
            continue
        recurrence_id = _as_aware_utc(getattr(component.get("RECURRENCE-ID"), "dt", None))
        if recurrence_id is not None:
            overrides[(uid, recurrence_id)] = component
        else:
            masters.append(component)

    for component in masters:
        yield from _expand(component, overrides, window_start, window_end, counter)

    # An override whose original instant fell outside the window still belongs in the window it
    # was MOVED to. Dropping these loses real meetings.
    for (uid, original), component in overrides.items():
        starts_at = _as_aware_utc(getattr(component.get("DTSTART"), "dt", None))
        if starts_at is None:
            counter.note("all_day_or_floating", uid)
            continue
        if not (window_start <= starts_at < window_end):
            continue
        event = _build(
            component,
            uid,
            starts_at,
            counter,
            series_id=uid,
            is_exception=True,
            original=original,
        )
        if event is not None:
            yield event


def _expand(
    component: Any,
    overrides: dict[tuple[str, dt.datetime], Any],
    window_start: dt.datetime,
    window_end: dt.datetime,
    dropped: Dropped,
) -> Iterator[RawEvent]:
    """Expand one VEVENT into the occurrences that fall inside the window."""
    uid = str(component.get("UID", "")).strip()
    # Deliberately NOT converted to UTC: see _as_aware. The expansion happens in the series'
    # own timezone, and only the resulting instants are converted.
    start = _as_aware(getattr(component.get("DTSTART"), "dt", None))
    end = _as_aware(getattr(component.get("DTEND"), "dt", None))
    if start is None:
        # All-day or floating. Skipped on purpose; see the module docstring.
        dropped.note("all_day_or_floating", uid)
        return
    if end is None:
        # A start with no usable end. RFC 5545 lets DTEND be omitted, but a meeting with no end
        # cannot answer "does it overlap", so it is refused rather than given a made-up length.
        dropped.note("no_end", uid)
        return

    duration = end - start
    rrule_value = component.get("RRULE")

    if rrule_value is None:
        if window_start <= start < window_end:
            event = _build(
                component,
                uid,
                start.astimezone(dt.UTC),
                dropped,
                series_id=None,
                is_exception=False,
            )
            if event is not None:
                yield event
        return

    excluded = _exdates(component)
    rule = rrulestr(rrule_value.to_ical().decode(), dtstart=start)

    for local_occurrence in rule.between(window_start, window_end, inc=True):
        # dateutil keeps the tzinfo and advances the LOCAL fields, so 10:00 stays 10:00 across
        # a DST boundary. Converting here, after expansion, is what makes that correct.
        occurrence = local_occurrence.astimezone(dt.UTC)
        if occurrence in excluded:
            continue
        # An edited occurrence is emitted by the override pass, at its NEW time. Skipping it
        # here is what stops the day showing the meeting twice.
        if (uid, occurrence) in overrides:
            continue
        event = _build(
            component,
            uid,
            occurrence,
            dropped,
            series_id=uid,
            is_exception=False,
            duration=duration,
        )
        if event is not None:
            yield event


def _exdates(component: Any) -> set[dt.datetime]:
    """Collect the instants a series explicitly does not occur on."""
    raw = component.get("EXDATE")
    if raw is None:
        return set()
    entries = raw if isinstance(raw, list) else [raw]
    out: set[dt.datetime] = set()
    for entry in entries:
        for value in getattr(entry, "dts", []):
            moment = _as_aware_utc(getattr(value, "dt", None))
            if moment is not None:
                out.add(moment)
    return out


def _build(
    component: Any,
    uid: str,
    starts_at: dt.datetime,
    dropped: Dropped,
    *,
    series_id: str | None,
    is_exception: bool,
    duration: dt.timedelta | None = None,
    original: dt.datetime | None = None,
) -> RawEvent | None:
    """Assemble one RawEvent, or None when the source data cannot support one.

    Every None returned here is counted in ``dropped`` with its reason.
    """
    if duration is None:
        end = _as_aware_utc(getattr(component.get("DTEND"), "dt", None))
        start = _as_aware_utc(getattr(component.get("DTSTART"), "dt", None))
        if start is None:
            dropped.note("all_day_or_floating", uid)
            return None
        if end is None:
            dropped.note("no_end", uid)
            return None
        duration = end - start
    if duration <= dt.timedelta(0):
        # A DST spring-forward can collapse a real-looking meeting to a single instant, and a
        # source can simply be wrong. Either way the database would refuse the row; refuse it
        # here, with a count, instead of letting the constraint be the messenger.
        dropped.note("zero_length", uid)
        return None

    payload = _raw_payload(component)
    if original is not None:
        payload["RECURRENCE-ID"] = original.isoformat()

    return RawEvent(
        source=SOURCE,
        external_id=_occurrence_id(uid, starts_at),
        series_id=series_id,
        is_exception=is_exception,
        title=str(component.get("SUMMARY")) if component.get("SUMMARY") else None,
        starts_at=starts_at,
        ends_at=starts_at + duration,
        status=str(component.get("STATUS", "confirmed")).lower(),
        organizer_email=_email(component.get("ORGANIZER")),
        attendees=_attendees(component),
        raw=payload,
    )
