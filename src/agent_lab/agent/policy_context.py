"""Build the policy engine's ``Context`` from the database, for one decision.

The engine is pure and reads nothing but the Context (``policy/engine.py``). This module is
the other half: every fact about the world a rule depends on is derived here, from rows, and
never taken from the model's proposal, because the proposal can be wrong or injected.

What is derived, and from what:

* **Meetings**: every non-cancelled meeting in the ISO week (Monday to Sunday, in ``tz``)
  of each proposed span. A week, not a day, because "keep one-to-ones on the same day" looks
  at the other one-to-ones, which are on other days by definition.
* **Fixed meetings**: a meeting's ``labels`` are the ``fixed_meeting_immutable`` row's
  markers that appear, case-insensitively and as whole words, in its title or in its ICS
  CATEGORIES. The markers are data; there is no "fixed" column.
* **One-to-ones and client calls**: a whole-word keyword match on the title, against the two
  keyword lists below. Known limit: a title is a weak signal. "Catch-up with Priya" is a
  one-to-one this does not see, and "Client list cleanup" is a client call it wrongly does.
* **Internal**: a meeting is internal when ingest did not mark it external. Internal people
  are the non-resource attendees of internal meetings, which mirrors how ingest decides
  externality (everyone shares the organizer's domain).
* **Confirmed tasks**: a task counts as confirmed done when ``tasks.agreed_by_me`` is set.
  The agent cannot set that column: no tool argument names it, and unknown keys are refused.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from functools import cache
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agent_lab.agent.policy.rules import LoadedPolicy, Markers, params_of
from agent_lab.agent.policy.types import Context, MeetingInfo
from agent_lab.models import Attendee, Meeting, People, Task

# The one place these keywords live. Matched as whole words, case-insensitively, in titles.
ONE_TO_ONE_KEYWORDS: tuple[str, ...] = (
    "1:1",
    "1-1",
    "1on1",
    "one-to-one",
    "one to one",
    "one-on-one",
    "one on one",
)
CLIENT_CALL_KEYWORDS: tuple[str, ...] = ("client", "customer")


@cache
def _pattern(keyword: str) -> re.Pattern[str]:
    # Lookarounds rather than \b, so a keyword that starts or ends with punctuation ("1:1")
    # still needs a non-word character (or the edge) on each side.
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)", re.IGNORECASE)


def keyword_hits(texts: Iterable[str | None], keywords: Iterable[str]) -> frozenset[str]:
    """The keywords (as written in the list, casefolded) that appear in any of ``texts``."""
    present = [t for t in texts if t]
    return frozenset(k.casefold() for k in keywords if any(_pattern(k).search(t) for t in present))


def is_one_to_one(title: str | None) -> bool:
    return bool(keyword_hits([title], ONE_TO_ONE_KEYWORDS))


def is_client_call(title: str | None) -> bool:
    return bool(keyword_hits([title], CLIENT_CALL_KEYWORDS))


def _markers(policies: Sequence[LoadedPolicy]) -> tuple[str, ...]:
    try:
        return params_of(policies, "fixed_meeting_immutable", Markers).markers
    except LookupError:
        return ()  # no such row: nothing can be labelled fixed, and no rule would read it


def _week_bounds(
    spans: Sequence[tuple[dt.datetime, dt.datetime]], zone: ZoneInfo
) -> tuple[dt.datetime, dt.datetime] | None:
    if not spans:
        return None
    first = min(s for s, _ in spans).astimezone(zone).date()
    last = max(e for _, e in spans).astimezone(zone).date()
    monday = first - dt.timedelta(days=first.isoweekday() - 1)
    next_monday = last + dt.timedelta(days=8 - last.isoweekday())
    return (
        dt.datetime.combine(monday, dt.time(), tzinfo=zone),
        dt.datetime.combine(next_monday, dt.time(), tzinfo=zone),
    )


def _categories(meeting: Meeting) -> list[str]:
    value = meeting.raw.get("CATEGORIES", []) if isinstance(meeting.raw, dict) else []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def build_context(
    session: Session,
    *,
    policies: Sequence[LoadedPolicy],
    now: dt.datetime,
    tz: str,
    spans: Sequence[tuple[dt.datetime, dt.datetime]] = (),
    task_ids: Iterable[int] = (),
) -> Context:
    """The facts for deciding writes that place ``spans`` and touch ``task_ids``."""
    zone = ZoneInfo(tz)
    meetings: list[Meeting] = []
    bounds = _week_bounds(spans, zone)
    if bounds is not None:
        # The range form, so the GiST span index serves it (as in tools_calendar).
        overlap = text(
            "tstzrange(meetings.starts_at, meetings.ends_at, '[)') && tstzrange(:s, :e, '[)')"
        )
        meetings = list(
            session.execute(
                select(Meeting)
                .where(overlap.bindparams(s=bounds[0], e=bounds[1]), Meeting.status != "cancelled")
                .order_by(Meeting.starts_at, Meeting.id)
            ).scalars()
        )

    markers = _markers(policies)
    infos = tuple(
        MeetingInfo(
            id=m.id,
            start=m.starts_at,
            end=m.ends_at,
            labels=keyword_hits([m.title, *_categories(m)], markers),
            is_internal=not m.is_external,
            is_one_to_one=is_one_to_one(m.title),
            is_client_call=is_client_call(m.title),
        )
        for m in meetings
    )

    internal_ids = [m.id for m in meetings if not m.is_external]
    internal_people: frozenset[str] = frozenset()
    if internal_ids:
        internal_people = frozenset(
            session.execute(
                select(People.email)
                .join(Attendee, Attendee.person_id == People.id)
                .where(Attendee.meeting_id.in_(internal_ids), People.is_resource.is_(False))
            ).scalars()
        )

    wanted = sorted(set(task_ids))
    confirmed: frozenset[int] = frozenset()
    if wanted:
        confirmed = frozenset(
            session.execute(
                select(Task.id).where(Task.id.in_(wanted), Task.agreed_by_me.is_(True))
            ).scalars()
        )

    return Context(
        now=now,
        tz=tz,
        meetings=infos,
        internal_people=internal_people,
        confirmed_done_task_ids=confirmed,
    )
