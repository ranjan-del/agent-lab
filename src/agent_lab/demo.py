"""`make demo`: load the Gate 1 demo week into the database, idempotently.

The committed fixture calendar (one week, five meetings) and one transcript of a client call
are ingested through the same code paths a real import takes: ``ics.parse`` plus
``ingest_events`` for the calendar, ``store_transcript`` for the transcript. Two choices make
it reproducible, where the plain ingest CLI is not:

* **A fixed window.** The ingest CLI windows around today, so a fixture week drifts out of it.
  Here the window is the fixture's own week, and the idempotency key names it.
* **The fake embedder.** ``make task`` searches with ``FakeEmbedder`` (no model download, no
  key), and ``search_transcripts`` only compares vectors from the same model. The transcript is
  embedded with it too, so the scripted run's search finds it.

Run it twice and the second run changes nothing: the calendar key is already recorded and the
transcript's checksum is already stored. Both are reported as skipped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from agent_lab.embeddings.fake import FakeEmbedder
from agent_lab.ingest.calendar import IngestCounts
from agent_lab.ingest.runner import AlreadyIngested, ingest_events
from agent_lab.ingest.sources import ics
from agent_lab.ingest.transcripts import store_transcript

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "demo"
CALENDAR = FIXTURES / "week-2026-10-05.ics"
TRANSCRIPT = FIXTURES / "acme-call-2026-10-05.txt"

TZ = ZoneInfo("Asia/Kolkata")
WEEK_START = dt.datetime(2026, 10, 5, tzinfo=TZ)  # Monday
WEEK_END = WEEK_START + dt.timedelta(days=7)
# The transcript belongs to the Acme call: its occurrence id, and captured when it ended.
TRANSCRIPT_MEETING = "demo-acme-call_20261005T103000Z"
TRANSCRIPT_CAPTURED_AT = dt.datetime(2026, 10, 5, 16, 45, tzinfo=TZ)


@dataclass(slots=True)
class DemoCounts:
    calendar_skipped: bool
    calendar: IngestCounts
    dropped: int
    transcript_skipped: bool
    chunks_written: int


def seed_demo(
    session: Session,
    *,
    now: dt.datetime,
    calendar: Path = CALENDAR,
    transcript: Path = TRANSCRIPT,
) -> DemoCounts:
    """Ingest the demo week and its transcript. The caller commits."""
    dropped = ics.Dropped()
    events = list(ics.parse(calendar, WEEK_START, WEEK_END, dropped=dropped))
    key = f"demo:{calendar.name}:{WEEK_START.date()}:{WEEK_END.date()}"
    skipped = False
    try:
        counts = ingest_events(session, events, source=ics.SOURCE, idempotency_key=key, now=now)
    except AlreadyIngested:
        skipped, counts = True, IngestCounts()

    stored = store_transcript(
        session,
        raw_text=transcript.read_text(encoding="utf-8"),
        embedder=FakeEmbedder(),
        now=now,
        source="txt",
        captured_at=TRANSCRIPT_CAPTURED_AT,
        meeting_external_id=TRANSCRIPT_MEETING,
    )
    return DemoCounts(
        calendar_skipped=skipped,
        calendar=counts,
        dropped=dropped.total,
        transcript_skipped=bool(stored.transcripts_skipped),
        chunks_written=stored.chunks_written,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-lab-demo", description=__doc__)
    parser.parse_args(argv)
    # Imported here, not at module import, so a test can set DATABASE_URL first.
    from agent_lab.db import SessionLocal

    with SessionLocal() as session:
        counts = seed_demo(session, now=dt.datetime.now(dt.UTC))
        session.commit()

    week = f"{WEEK_START.date()} to {(WEEK_END - dt.timedelta(days=1)).date()}"
    if counts.calendar_skipped:
        print(f"calendar   : already loaded ({CALENDAR.name}, week {week}), skipped")
    else:
        c = counts.calendar
        print(
            f"calendar   : {CALENDAR.name}, week {week}: {c.meetings_inserted} inserted, "
            f"{c.meetings_updated} updated, {counts.dropped} dropped"
        )
    if counts.transcript_skipped:
        print(f"transcript : already stored ({TRANSCRIPT.name}), skipped")
    else:
        print(
            f"transcript : {TRANSCRIPT.name}: {counts.chunks_written} chunks, embedded with "
            f"{FakeEmbedder().name}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
