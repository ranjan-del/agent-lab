"""The source-agnostic shape every calendar source must produce.

Sources differ wildly (an .ics export, the Google Calendar API, an Exchange feed). They all
normalise to ``RawEvent`` before anything touches the database, so the upsert logic is written
once and every source inherits its correctness, including the idempotency guarantee.

One occurrence is one ``RawEvent``. A weekly standup that recurs twelve times is twelve of
these, because the schema stores expanded occurrences.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RawAttendee:
    """One participant on one occurrence.

    ``is_resource`` distinguishes a bookable room from a human. A room that is treated as a
    person will trigger a spurious double-booking violation, which is a real failure mode and
    the reason this flag is carried all the way from the source rather than guessed later.
    """

    email: str
    display_name: str | None = None
    response_status: str = "needsAction"
    is_optional: bool = False
    is_organizer: bool = False
    is_resource: bool = False


@dataclass(frozen=True, slots=True)
class RawEvent:
    """One occurrence of a calendar event, normalised.

    ``external_id`` must be stable across re-imports and unique per occurrence: that pairing
    with ``source`` is what the database's unique constraint enforces, and therefore what makes
    re-running ingestion a no-op rather than a duplication.
    """

    source: str
    external_id: str
    starts_at: dt.datetime
    ends_at: dt.datetime
    title: str | None = None
    series_id: str | None = None
    is_exception: bool = False
    status: str = "confirmed"
    organizer_email: str | None = None
    attendees: tuple[RawAttendee, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject anything the database would reject, but with a message that names the event.

        A constraint violation deep inside a bulk insert tells you a row was bad. This tells
        you which one, before the transaction is even opened.
        """
        for name, value in (("starts_at", self.starts_at), ("ends_at", self.ends_at)):
            if value.tzinfo is None:
                raise ValueError(f"{self.external_id}: {name} is naive; every time must be aware")
        if self.ends_at <= self.starts_at:
            raise ValueError(
                f"{self.external_id}: ends_at {self.ends_at} "
                f"is not after starts_at {self.starts_at}"
            )
