"""The policy Context, built from the database for one decision. Against a real Postgres.

The engine reads nothing but the Context, so every fact a rule depends on (which meeting is
fixed, which is a one-to-one or a client call, who is internal, which task I confirmed) is
derived here, from the rows, and never taken from the model's proposal.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from agent_lab.agent.policy.rules import load_policies
from agent_lab.agent.policy_context import build_context, is_client_call, is_one_to_one
from agent_lab.models import Attendee, Meeting, People, Task
from policy_seed import seed_policies

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 9, 21, 8, tzinfo=IST)  # Monday
TZ = "Asia/Kolkata"


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, day, hour, minute, tzinfo=IST)


def _meeting(
    session: Session,
    title: str,
    start: dt.datetime,
    minutes: int = 30,
    *,
    raw: dict[str, Any] | None = None,
    status: str = "confirmed",
    is_external: bool = False,
) -> Meeting:
    m = Meeting(
        source="test",
        external_id=f"{title}-{start.isoformat()}",
        title=title,
        starts_at=start,
        ends_at=start + dt.timedelta(minutes=minutes),
        status=status,
        is_external=is_external,
        raw=raw or {},
        ingested_at=NOW,
    )
    session.add(m)
    session.flush()
    return m


def _person(session: Session, email: str, *, resource: bool = False) -> People:
    p = People(email=email, is_resource=resource, created_at=NOW)
    session.add(p)
    session.flush()
    return p


def _context(session: Session, span_day: int = 23, **kw: Any) -> Any:
    return build_context(
        session,
        policies=kw.pop("policies", load_policies(session)),
        now=NOW,
        tz=TZ,
        spans=[(at(span_day, 15), at(span_day, 15, 30))],
        **kw,
    )


def test_fixed_markers_match_title_and_ics_categories_case_insensitively(
    session: Session,
) -> None:
    by_title = _meeting(session, "Board review (IMPORTANT)", at(22, 14))
    by_category = _meeting(session, "Quarterly sync", at(23, 12), raw={"CATEGORIES": ["Fixed"]})
    plain = _meeting(session, "Unfixed agenda chat", at(24, 12))  # a word inside a word

    labels = {m.id: m.labels for m in _context(session).meetings}

    assert labels[by_title.id] == frozenset({"important"})
    assert labels[by_category.id] == frozenset({"fixed"})
    assert labels[plain.id] == frozenset()


def test_the_markers_are_read_from_the_row_so_an_edit_changes_the_labels(
    session: Session,
) -> None:
    sacred = _meeting(session, "Sacred planning", at(22, 14))
    policies = seed_policies(fixed_meeting_immutable={"markers": ["sacred"]})

    (info,) = _context(session, policies=policies).meetings

    assert info.id == sacred.id and info.labels == frozenset({"sacred"})


def test_one_to_ones_and_client_calls_are_title_keyword_matches() -> None:
    assert is_one_to_one("1:1 with Sriram") and is_one_to_one("Weekly one-to-one")
    assert is_one_to_one("One on one: Priya")
    assert not is_one_to_one("Team sync 11:15")
    assert is_client_call("Client call with Acme") and is_client_call("Customer demo")
    assert not is_client_call("Clientele report review")
    assert not is_one_to_one(None) and not is_client_call(None)


def test_meetings_are_the_iso_week_of_the_proposal_without_cancelled_ones(
    session: Session,
) -> None:
    monday = _meeting(session, "1:1 with Sriram", at(21, 12))
    sunday = _meeting(session, "Client call", at(27, 12))
    _meeting(session, "Last week", at(20, 12))
    _meeting(session, "Next week", at(28, 12))
    _meeting(session, "Called off", at(23, 12), status="cancelled")

    context = _context(session)

    assert [m.id for m in context.meetings] == [monday.id, sunday.id]
    assert context.meetings[0].is_one_to_one and not context.meetings[0].is_client_call
    assert context.meetings[1].is_client_call
    assert context.tz == TZ and context.now == NOW


def test_no_span_means_no_meetings_are_loaded(session: Session) -> None:
    _meeting(session, "Standup", at(23, 12))
    context = build_context(session, policies=load_policies(session), now=NOW, tz=TZ)
    assert context.meetings == ()


def test_internal_people_are_the_humans_in_internal_meetings(session: Session) -> None:
    internal = _meeting(session, "Design review", at(22, 12))
    external = _meeting(session, "Vendor call", at(22, 14), is_external=True)
    me, colleague = _person(session, "me@example.org"), _person(session, "sriram@example.org")
    room = _person(session, "room@resource.example.org", resource=True)
    buyer = _person(session, "buyer@bigcorp.example")
    for m, p in [(internal, me), (internal, colleague), (internal, room), (external, buyer)]:
        session.add(Attendee(meeting_id=m.id, person_id=p.id))
    session.flush()

    context = _context(session, span_day=22)

    assert context.internal_people == frozenset({"me@example.org", "sriram@example.org"})
    by_id = {m.id: m for m in context.meetings}
    assert by_id[internal.id].is_internal and not by_id[external.id].is_internal


def test_confirmed_tasks_are_the_ones_with_agreed_by_me_set(session: Session) -> None:
    mine = Task(text="Send deck", agreed_by_me=True, created_at=NOW)
    not_mine = Task(text="Send notes", agreed_by_me=False, created_at=NOW)
    session.add_all([mine, not_mine])
    session.flush()

    context = build_context(
        session,
        policies=load_policies(session),
        now=NOW,
        tz=TZ,
        task_ids=[mine.id, not_mine.id, 999_999],
    )

    assert context.confirmed_done_task_ids == frozenset({mine.id})
