"""Wrap an import in an audited, idempotent run.

Two things this buys that a bare loop does not:

* **"Did this already run?" becomes a database question** rather than a guess, because the
  idempotency key carries a unique constraint.
* **A failure leaves a row saying what broke**, instead of leaving silence. Silence is the
  worst outcome of a nightly job, because nobody notices it for a week.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.ingest.calendar import IngestCounts, upsert_event
from agent_lab.ingest.types import RawEvent
from agent_lab.models import IngestionRun


class AlreadyIngested(Exception):
    """Raised when an idempotency key has already completed successfully."""


def ingest_events(
    session: Session,
    events: Iterable[RawEvent],
    *,
    source: str,
    idempotency_key: str,
    now: dt.datetime,
    force: bool = False,
) -> IngestCounts:
    """Import events under an audited run.

    The upserts are idempotent on their own, so re-running is always *safe*. The key exists so
    a re-run can also be *skipped*, which matters when the import is expensive.
    """
    prior = session.execute(
        select(IngestionRun).where(IngestionRun.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if prior is not None and prior.status == "succeeded" and not force:
        raise AlreadyIngested(f"{idempotency_key} already succeeded at {prior.finished_at}")

    run = prior or IngestionRun(source=source, idempotency_key=idempotency_key, started_at=now)
    run.started_at = now
    run.status = "running"
    run.error = None
    session.add(run)
    session.flush()

    totals = IngestCounts()
    try:
        for event in events:
            counts = upsert_event(session, event, now)
            totals.meetings_inserted += counts.meetings_inserted
            totals.meetings_updated += counts.meetings_updated
            totals.people_created += counts.people_created
            totals.attendees_written += counts.attendees_written
    except Exception as exc:
        # The run row is written on its own connection state; the caller decides what to do
        # with the transaction. Recording the failure is not optional.
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = now
        raise

    run.status = "succeeded"
    run.finished_at = now
    run.rows_written = totals.rows_written
    session.flush()
    return totals
