"""What the policy engine is asked about, what it knows, and what it answers.

Three groups of plain frozen dataclasses, no I/O and no ORM:

* ``ProposedAction``: one write the agent wants to make. Each kind carries only what the
  agent proposes. Anything about the world (which meeting is fixed, who is internal, which
  task I confirmed) is NOT on the action, because the action comes from the model and the
  model can be wrong or injected. Those facts live on ``Context``.
* ``Context``: the facts the caller loaded from the database before asking. The engine
  reads nothing else.
* ``Decision``: ALLOW, REFUSE or ASK_OVERRIDE, carrying the rule that decided it.

Every instant must be timezone-aware. A naive datetime cannot be placed in IST or anywhere
else, so it is refused at construction rather than guessed at inside a rule.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from agent_lab.agent.policy.rules import Tier


def _aware(name: str, value: dt.datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {value.isoformat()}")


def _ordered(start: dt.datetime, end: dt.datetime) -> None:
    _aware("start", start)
    _aware("end", end)
    if end <= start:
        raise ValueError(f"end {end.isoformat()} must be after start {start.isoformat()}")


# ---------------------------------------------------------------------------------------------
# Proposed actions
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PlaceSlot:
    """Put something new on the calendar, or propose a time for it."""

    start: dt.datetime
    end: dt.datetime
    attendees: tuple[str, ...] = ()
    title: str | None = None
    is_one_to_one: bool = False
    is_client_call: bool = False

    def __post_init__(self) -> None:
        _ordered(self.start, self.end)


@dataclass(frozen=True, slots=True)
class ChangeMeeting:
    """Move or shorten an existing meeting. Its current facts come from ``Context.meetings``."""

    meeting_id: int
    new_start: dt.datetime
    new_end: dt.datetime

    def __post_init__(self) -> None:
        _ordered(self.new_start, self.new_end)


@dataclass(frozen=True, slots=True)
class AddAttendee:
    """Invite one more person to an existing meeting."""

    meeting_id: int
    email: str


@dataclass(frozen=True, slots=True)
class ExportContent:
    """Send something outside the database. No tool does this in this version (SPEC 7)."""

    destination: str
    includes_transcript: bool


@dataclass(frozen=True, slots=True)
class TaskChange:
    """Create (``task_id`` None) or update one task row. ``new_status`` None leaves it."""

    task_id: int | None
    new_status: Literal["open", "dropped", "done"] | None = None


ProposedAction = PlaceSlot | ChangeMeeting | AddAttendee | ExportContent | TaskChange


# ---------------------------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MeetingInfo:
    """One meeting as the engine needs it. ``labels`` are the words a fixed-marker can match."""

    id: int
    start: dt.datetime
    end: dt.datetime
    labels: frozenset[str] = frozenset()
    is_internal: bool = True
    is_one_to_one: bool = False
    is_client_call: bool = False

    def __post_init__(self) -> None:
        _ordered(self.start, self.end)


@dataclass(frozen=True, slots=True)
class Context:
    """Everything the engine may consult, loaded by the caller.

    ``tz`` is the zone a "day" is counted in for the rules whose parameters carry no zone of
    their own (meetings and hours per day, the lightest day). Rules with a ``tz`` parameter
    use theirs. ``meetings`` is the calendar around the proposal, excluding cancelled ones.
    ``internal_people`` is who is internal; anyone not in it is treated as external.
    ``candidate_days`` are the days the agent chose between, for the lightest-day preference.
    """

    now: dt.datetime
    tz: str
    meetings: tuple[MeetingInfo, ...] = ()
    internal_people: frozenset[str] = frozenset()
    confirmed_done_task_ids: frozenset[int] = frozenset()
    candidate_days: tuple[dt.date, ...] = ()

    def __post_init__(self) -> None:
        _aware("now", self.now)
        ZoneInfo(self.tz)  # raises on an unknown zone, before any rule runs

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def meeting(self, meeting_id: int) -> MeetingInfo:
        for m in self.meetings:
            if m.id == meeting_id:
                return m
        raise IncompleteContext(
            f"meeting {meeting_id} is not in the context; the caller must load it before "
            "asking, because the engine cannot check a meeting it cannot see"
        )


class IncompleteContext(ValueError):
    """The caller asked about something the context does not describe. A caller bug."""


# ---------------------------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------------------------
class Outcome(StrEnum):
    ALLOW = "allow"
    REFUSE = "refuse"
    ASK_OVERRIDE = "ask_override"


@dataclass(frozen=True, slots=True)
class RuleHit:
    """One rule that fired: which row, its tier, its sentence, and the facts that fired it."""

    policy_id: int
    code: str
    tier: Tier
    description: str
    detail: str


@dataclass(frozen=True, slots=True)
class Advisory:
    """A soft rule the agent set aside. ``note`` is the sentence the agent must say."""

    policy_id: int
    code: str
    description: str
    detail: str

    @property
    def note(self) -> str:
        return f"Preference set aside ({self.code}): {self.description} {self.detail}"


@dataclass(frozen=True, slots=True)
class Decision:
    """The engine's answer.

    ALLOW carries ``notes`` from any soft rule set aside, and no ``hit``. REFUSE carries the
    hard rule in ``hit``. ASK_OVERRIDE carries the middle rule in ``hit`` and, in ``needs``,
    what would have to be true to proceed. ``also`` lists the other blocking rules that fired,
    so one refusal or one question can mention all of them.
    """

    outcome: Outcome
    hit: RuleHit | None = None
    also: tuple[RuleHit, ...] = ()
    needs: str | None = None
    notes: tuple[Advisory, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome is Outcome.ALLOW:
            if self.hit is not None or self.also or self.needs is not None:
                raise ValueError("ALLOW carries notes only")
            return
        if self.hit is None or self.notes:
            raise ValueError(f"{self.outcome} needs a rule and carries no notes")
        if self.outcome is Outcome.REFUSE and (self.hit.tier != "hard" or self.needs is not None):
            raise ValueError("REFUSE is decided by a hard rule and has no override")
        if self.outcome is Outcome.ASK_OVERRIDE and (self.hit.tier != "middle" or not self.needs):
            raise ValueError("ASK_OVERRIDE is decided by a middle rule and says what it needs")

    @property
    def code(self) -> str | None:
        return self.hit.code if self.hit else None

    @property
    def tier(self) -> Tier | None:
        return self.hit.tier if self.hit else None

    @property
    def reason(self) -> str | None:
        """The rule's own description, quoted back: the stated reason SPEC section 3 asks for."""
        return self.hit.description if self.hit else None

    @property
    def detail(self) -> str:
        return self.hit.detail if self.hit else ""

    @property
    def policy_id(self) -> int | None:
        return self.hit.policy_id if self.hit else None
