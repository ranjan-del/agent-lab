"""The policy engine: one pure function from (action, context, rows) to a Decision.

No model call, no database session, no clock, no I/O. Every number a rule compares against
comes from its row's parsed parameters (``rules.py``); the only thing this module knows about
a rule is how to test it, looked up by the rule's ``code``. The tier that decides what a firing
rule does comes from the row too, so moving a rule between tiers is a data edit.

Same action plus same context plus same rows gives the same Decision, every time, whatever
order the rows arrive in. W4's eval harness replays against exactly that property.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Callable, Iterable

from pydantic import BaseModel

from agent_lab.agent.policy.rules import (
    DailyWindow,
    LoadedPolicy,
    Markers,
    MaxCount,
    MaxHours,
    Minutes,
    NoParams,
    NoticeHours,
    UnknownRule,
)
from agent_lab.agent.policy.types import (
    AddAttendee,
    ChangeMeeting,
    Context,
    Decision,
    ExportContent,
    MeetingInfo,
    Outcome,
    PlaceSlot,
    ProposedAction,
    RuleHit,
    TaskChange,
)

# A rule's test: None when it does not fire, else the facts that made it fire, in words.
Check = Callable[[LoadedPolicy, ProposedAction, Context], str | None]

_RANK = {"hard": 0, "middle": 1, "soft": 2}
_DAY = dt.timedelta(days=1)


def evaluate(
    action: ProposedAction, context: Context, policies: Iterable[LoadedPolicy]
) -> Decision:
    """Decide one proposed write against every active rule.

    Precedence, when several rules fire: hard beats middle beats soft. Any hard rule firing
    is a REFUSE, whatever else fired, because a hard rule has no override and asking me about
    a middle rule first would offer a yes that cannot be honoured. Otherwise any middle rule
    firing is an ASK_OVERRIDE, and soft notes are dropped because the action has not been
    allowed yet. Only an ALLOW carries soft notes. Within a tier the lowest policy id decides,
    and the rest are listed in ``also`` so one answer can name every rule that fired.

    Inactive rows are skipped. A row whose code has no test here raises ``UnknownRule``: a
    rule nobody can evaluate must not pass as harmless.
    """
    ordered = sorted((p for p in policies if p.active), key=lambda p: (_RANK[p.tier], p.id, p.code))
    hits: list[RuleHit] = []
    for policy in ordered:
        check = _CHECKS.get(policy.code)
        if check is None:
            raise UnknownRule(f"policy {policy.code!r} has no test in the engine")
        detail = check(policy, action, context)
        if detail is not None:
            hits.append(
                RuleHit(
                    policy_id=policy.id,
                    code=policy.code,
                    tier=policy.tier,
                    description=policy.description,
                    detail=detail,
                )
            )

    hard = [h for h in hits if h.tier == "hard"]
    middle = [h for h in hits if h.tier == "middle"]
    if hard:
        return Decision(Outcome.REFUSE, hit=hard[0], also=tuple(hard[1:] + middle))
    if middle:
        return Decision(
            Outcome.ASK_OVERRIDE, hit=middle[0], also=tuple(middle[1:]), needs=_needs(middle)
        )
    return Decision(Outcome.ALLOW)


def _needs(middle: list[RuleHit]) -> str:
    codes = ", ".join(h.code for h in middle)
    facts = "; ".join(h.detail for h in middle)
    return (
        f"Proceed only if this case is top-urgent and I approve breaking {codes} "
        f"for it specifically. What would be broken: {facts}."
    )


# ---------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------
def _params[S: BaseModel](policy: LoadedPolicy, shape: type[S]) -> S:
    if not isinstance(policy.params, shape):
        raise TypeError(
            f"policy {policy.code!r} has params {type(policy.params).__name__}, "
            f"expected {shape.__name__}"
        )
    return policy.params


def _span(action: ProposedAction) -> tuple[dt.datetime, dt.datetime] | None:
    """The interval an action puts on the calendar, if it puts one there."""
    if isinstance(action, PlaceSlot):
        return action.start, action.end
    if isinstance(action, ChangeMeeting):
        return action.new_start, action.new_end
    return None


def _hhmm(t: dt.time) -> str:
    return t.strftime("%H:%M")


def _describe_span(start: dt.datetime, end: dt.datetime, tz: str, zone: dt.tzinfo) -> str:
    s, e = start.astimezone(zone), end.astimezone(zone)
    return f"{s:%a %Y-%m-%d %H:%M} to {e:%H:%M} {tz}"


def _window_overlap(
    start: dt.datetime, end: dt.datetime, window: DailyWindow
) -> tuple[dt.datetime, dt.datetime] | None:
    """The first occurrence of ``window`` that ``[start, end)`` overlaps, in the window's zone."""
    zone = window.zone
    day = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    while day <= last:
        if day.isoweekday() in window.days:
            w_start = dt.datetime.combine(day, window.start, tzinfo=zone)
            w_end = dt.datetime.combine(day, window.end, tzinfo=zone)
            if start < w_end and w_start < end:
                return w_start, w_end
        day += _DAY
    return None


def _window_text(window: DailyWindow) -> str:
    days = ", ".join(calendar.day_abbr[d - 1] for d in window.days)
    return f"{_hhmm(window.start)} to {_hhmm(window.end)} {window.tz}, {days}"


def _inside_window(start: dt.datetime, end: dt.datetime, window: DailyWindow) -> bool:
    """True when ``[start, end)`` sits wholly inside one occurrence of ``window``."""
    zone = window.zone
    day = start.astimezone(zone).date()
    if day.isoweekday() not in window.days:
        return False
    w_start = dt.datetime.combine(day, window.start, tzinfo=zone)
    w_end = dt.datetime.combine(day, window.end, tzinfo=zone)
    return w_start <= start and end <= w_end


def _replaced(action: ProposedAction) -> int | None:
    """The meeting an action moves, which must not be counted against its own new slot."""
    return action.meeting_id if isinstance(action, ChangeMeeting) else None


def _day_of(instant: dt.datetime, context: Context) -> dt.date:
    return instant.astimezone(context.zone).date()


def _others(action: ProposedAction, context: Context) -> list[MeetingInfo]:
    replaced = _replaced(action)
    return [m for m in context.meetings if m.id != replaced]


def _same_day(action: ProposedAction, context: Context, day: dt.date) -> list[MeetingInfo]:
    return [m for m in _others(action, context) if _day_of(m.start, context) == day]


def _is_noop(action: ChangeMeeting, current: MeetingInfo) -> bool:
    return (action.new_start, action.new_end) == (current.start, current.end)


# ---------------------------------------------------------------------------------------------
# Hard rules
# ---------------------------------------------------------------------------------------------
def _fixed_meeting_immutable(
    policy: LoadedPolicy, action: ProposedAction, context: Context
) -> str | None:
    if not isinstance(action, ChangeMeeting):
        return None
    params = _params(policy, Markers)
    current = context.meeting(action.meeting_id)
    if _is_noop(action, current):
        return None
    markers = {m.casefold() for m in params.markers}
    matched = sorted(label for label in current.labels if label.casefold() in markers)
    if not matched:
        return None
    return f"meeting {current.id} is labelled {', '.join(matched)}"


def _focus_block(policy: LoadedPolicy, action: ProposedAction, context: Context) -> str | None:
    span = _span(action)
    if span is None:
        return None
    window = _params(policy, DailyWindow)
    overlap = _window_overlap(*span, window)
    if overlap is None:
        return None
    return (
        f"{_describe_span(*span, window.tz, window.zone)} overlaps the block "
        f"{_window_text(window)} on {overlap[0]:%a %Y-%m-%d}"
    )


def _no_external_exposure(
    policy: LoadedPolicy, action: ProposedAction, context: Context
) -> str | None:
    _params(policy, NoParams)
    if isinstance(action, ExportContent):
        if action.includes_transcript:
            return f"transcript content would leave the database for {action.destination}"
        return None
    if not isinstance(action, AddAttendee):
        return None
    target = context.meeting(action.meeting_id)
    internal = {person.casefold() for person in context.internal_people}
    if target.is_internal and action.email.casefold() not in internal:
        return f"{action.email} is not internal and meeting {target.id} is an internal meeting"
    return None


def _never_mark_done(policy: LoadedPolicy, action: ProposedAction, context: Context) -> str | None:
    _params(policy, NoParams)
    if not isinstance(action, TaskChange) or action.new_status != "done":
        return None
    if action.task_id is not None and action.task_id in context.confirmed_done_task_ids:
        return None
    which = "a new task" if action.task_id is None else f"task {action.task_id}"
    return f"{which} would be marked done without my confirmation"


# ---------------------------------------------------------------------------------------------
# Middle rules
# ---------------------------------------------------------------------------------------------
def _max_meetings_per_day(
    policy: LoadedPolicy, action: ProposedAction, context: Context
) -> str | None:
    span = _span(action)
    if span is None:
        return None
    params = _params(policy, MaxCount)
    day = _day_of(span[0], context)
    count = len(_same_day(action, context, day)) + 1
    if count <= params.max:
        return None
    return f"{day:%a %Y-%m-%d} would hold {count} meetings, the limit is {params.max}"


def _max_meeting_hours_per_day(
    policy: LoadedPolicy, action: ProposedAction, context: Context
) -> str | None:
    span = _span(action)
    if span is None:
        return None
    params = _params(policy, MaxHours)
    day = _day_of(span[0], context)
    booked = sum((m.end - m.start for m in _same_day(action, context, day)), dt.timedelta())
    total = booked + (span[1] - span[0])
    if total <= dt.timedelta(hours=params.max_hours):
        return None
    hours = total / dt.timedelta(hours=1)
    return (
        f"{day:%a %Y-%m-%d} would hold {hours:g} hours of meetings, "
        f"the limit is {params.max_hours:g}"
    )


def _min_gap_between_meetings(
    policy: LoadedPolicy, action: ProposedAction, context: Context
) -> str | None:
    span = _span(action)
    if span is None:
        return None
    params = _params(policy, Minutes)
    start, end = span
    minimum = dt.timedelta(minutes=params.minutes)
    # Negative gap means overlap. The closest meeting is reported; ties go to the lowest id.
    gaps = sorted((max(m.start - end, start - m.end), m.id) for m in _others(action, context))
    if not gaps or gaps[0][0] >= minimum:
        return None
    gap, mid = gaps[0]
    if gap < dt.timedelta():
        return f"it overlaps meeting {mid}; the minimum gap is {params.minutes} minutes"
    minutes = int(gap / dt.timedelta(minutes=1))
    return (
        f"it is {minutes} minutes from meeting {mid}; the minimum gap is {params.minutes} minutes"
    )


def _working_hours(policy: LoadedPolicy, action: ProposedAction, context: Context) -> str | None:
    span = _span(action)
    if span is None:
        return None
    window = _params(policy, DailyWindow)
    if _inside_window(*span, window):
        return None
    return f"{_describe_span(*span, window.tz, window.zone)} is outside {_window_text(window)}"


def _no_change_within_notice(
    policy: LoadedPolicy, action: ProposedAction, context: Context
) -> str | None:
    if not isinstance(action, ChangeMeeting):
        return None
    params = _params(policy, NoticeHours)
    current = context.meeting(action.meeting_id)
    if _is_noop(action, current):
        return None
    lead = current.start - context.now
    if lead >= dt.timedelta(hours=params.hours):
        return None
    hours = round(lead / dt.timedelta(hours=1), 1)
    return (
        f"meeting {current.id} starts {hours:g} hours from now, "
        f"inside the {params.hours}-hour notice"
    )


_CHECKS: dict[str, Check] = {
    "fixed_meeting_immutable": _fixed_meeting_immutable,
    "focus_block": _focus_block,
    "no_external_exposure": _no_external_exposure,
    "never_mark_done": _never_mark_done,
    "max_meetings_per_day": _max_meetings_per_day,
    "max_meeting_hours_per_day": _max_meeting_hours_per_day,
    "min_gap_between_meetings": _min_gap_between_meetings,
    "working_hours": _working_hours,
    "no_change_within_notice": _no_change_within_notice,
}
