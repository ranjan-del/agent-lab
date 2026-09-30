"""What each ``policies.rule`` JSONB must contain, and how to load the rows safely.

The database stores the rule parameters as JSONB so a number can change without a release.
The price is that Postgres cannot check the shape. These models pay that price: every row is
validated through the shape registered for its code the moment it is read, so a data edit
that breaks a rule fails at load time with the code in the message, not inside the engine
as a wrong decision.

Nothing here decides anything. Deciding is ``engine.py``. This file only answers "is this
row well-formed, and what are its parameters as real Python values".
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    # Only the loader needs the database, and it imports it itself. The engine imports this
    # module for the shapes, and its import graph must stay free of sessions and models.
    from sqlalchemy.orm import Session

Tier = Literal["hard", "middle", "soft"]
TIERS: tuple[Tier, ...] = ("hard", "middle", "soft")


class _Shape(BaseModel):
    """Every shape forbids unknown keys. A misspelt key must fail, not vanish."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NoParams(_Shape):
    """A rule that needs no numbers. The row still exists so it can be switched off."""


class Markers(_Shape):
    """Words in a meeting that mark it as untouchable. Even the words are data."""

    markers: tuple[str, ...] = Field(min_length=1)


class DailyWindow(_Shape):
    """A wall-clock window on given ISO weekdays in a named zone.

    Stored as "HH:MM" strings and weekday ints, because that is what a human edits. Parsed
    to ``time`` and ``ZoneInfo`` so the engine never touches a string.
    """

    start: dt.time
    end: dt.time
    days: tuple[int, ...] = Field(min_length=1)
    tz: str

    @field_validator("days")
    @classmethod
    def _iso_weekdays(cls, days: tuple[int, ...]) -> tuple[int, ...]:
        bad = [d for d in days if not 1 <= d <= 7]
        if bad:
            raise ValueError(f"days must be ISO weekdays 1 to 7, got {bad}")
        return tuple(sorted(set(days)))

    @field_validator("tz")
    @classmethod
    def _real_zone(cls, tz: str) -> str:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"tz {tz!r} is not a known timezone") from exc
        return tz

    @model_validator(mode="after")
    def _end_after_start(self) -> DailyWindow:
        if self.end <= self.start:
            raise ValueError(f"end {self.end} must be after start {self.start}")
        return self

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def describe(self) -> str:
        """The window in words, e.g. "08:00 to 10:30 UTC, Mon, Wed"."""
        days = ", ".join(calendar.day_abbr[d - 1] for d in self.days)
        return f"{self.start:%H:%M} to {self.end:%H:%M} {self.tz}, {days}"


class MaxCount(_Shape):
    max: int = Field(gt=0)


class MaxHours(_Shape):
    max_hours: float = Field(gt=0)


class Minutes(_Shape):
    minutes: int = Field(gt=0)


class NoticeHours(_Shape):
    hours: int = Field(gt=0)


# One entry per SPEC section 3 rule, in SPEC order. Adding a rule means adding a row in a
# migration AND a line here; a row with no shape refuses to load, on purpose.
RULE_SHAPES: dict[str, type[_Shape]] = {
    # hard
    "fixed_meeting_immutable": Markers,
    "focus_block": DailyWindow,
    "no_external_exposure": NoParams,
    "never_mark_done": NoParams,
    # middle
    "max_meetings_per_day": MaxCount,
    "max_meeting_hours_per_day": MaxHours,
    "min_gap_between_meetings": Minutes,
    "working_hours": DailyWindow,
    "no_change_within_notice": NoticeHours,
    # soft
    "prefer_lightest_day": NoParams,
    "keep_one_to_ones_same_day": NoParams,
    "prefer_short_slots": Minutes,
    "client_calls_late_morning": DailyWindow,
}


class UnknownRule(LookupError):
    """A ``policies`` row whose code has no registered shape. Data nobody can evaluate."""


def parse_rule(code: str, rule: dict[str, Any]) -> _Shape:
    """Validate one rule's parameters against the shape registered for its code."""
    shape = RULE_SHAPES.get(code)
    if shape is None:
        raise UnknownRule(f"policy {code!r} has no registered shape; known: {sorted(RULE_SHAPES)}")
    return shape.model_validate(rule)


@dataclass(frozen=True, slots=True)
class LoadedPolicy:
    """One row, validated. What the engine receives; it never sees the ORM object."""

    id: int
    code: str
    tier: Tier
    description: str
    params: _Shape
    active: bool


def params_of[S: _Shape](policies: Iterable[LoadedPolicy], code: str, shape: type[S]) -> S:
    """The parsed parameters of the row ``code``, whether or not it is active.

    For code that needs a rule's numbers outside the engine (the prompt, a tool description,
    the calendar's working hours), so those numbers are read from the row and never retyped.
    A missing row is an error naming the code, not a silent default.
    """
    for policy in policies:
        if policy.code == code:
            if not isinstance(policy.params, shape):
                raise TypeError(
                    f"policy {code!r} has params {type(policy.params).__name__}, "
                    f"expected {shape.__name__}"
                )
            return policy.params
    raise LookupError(f"policy {code!r} is not loaded; its numbers cannot be read")


def load_policies(session: Session, *, include_inactive: bool = True) -> list[LoadedPolicy]:
    """Read every row and validate it. One bad row fails the whole load, naming the row.

    Inactive rows are loaded by default so a caller can show them; the engine skips them.
    """
    from sqlalchemy import select

    from agent_lab.models import Policy

    statement = select(Policy).order_by(Policy.id)
    if not include_inactive:
        statement = statement.where(Policy.active.is_(True))
    out: list[LoadedPolicy] = []
    for row in session.execute(statement).scalars():
        if row.kind not in TIERS:
            raise ValueError(f"policy {row.code!r} has tier {row.kind!r}, expected one of {TIERS}")
        tier: Tier = row.kind
        out.append(
            LoadedPolicy(
                id=row.id,
                code=row.code,
                tier=tier,
                description=row.description,
                params=parse_rule(row.code, row.rule),
                active=row.active,
            )
        )
    return out
