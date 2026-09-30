"""Command line entry point: ``uv run python -m agent_lab.ingest.cli calendar <file.ics>``.

Prints counts, because a job that says "done" tells you nothing and a job that says
"41 meetings, 12 people, 96 attendees" tells you whether it worked.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from agent_lab.db import SessionLocal
from agent_lab.embeddings import get_embedder
from agent_lab.ingest.runner import AlreadyIngested, ingest_events
from agent_lab.ingest.sources import ics
from agent_lab.ingest.transcripts import store_transcript


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-lab-ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    cal = sub.add_parser("calendar", help="import an .ics export")
    cal.add_argument("path", type=Path)
    cal.add_argument("--days-back", type=int, default=30)
    cal.add_argument("--days-forward", type=int, default=60)
    cal.add_argument("--force", action="store_true", help="re-run even if the key succeeded")

    tr = sub.add_parser("transcript", help="store a transcript: chunk, embed, write")
    tr.add_argument("path", type=Path)
    tr.add_argument("--meeting", help="external_id of the meeting this belongs to")
    tr.add_argument("--embedder", default=None, help="local (default) or fake")

    args = parser.parse_args(argv)
    if args.command == "transcript":
        return _transcript(args)
    now = dt.datetime.now(dt.UTC)
    window_start = now - dt.timedelta(days=args.days_back)
    window_end = now + dt.timedelta(days=args.days_forward)

    dropped = ics.Dropped()
    events = list(ics.parse(args.path, window_start, window_end, dropped=dropped))
    key = f"ics:{args.path.name}:{window_start.date()}:{window_end.date()}"

    with SessionLocal() as session:
        try:
            counts = ingest_events(
                session,
                events,
                source=ics.SOURCE,
                idempotency_key=key,
                now=now,
                force=args.force,
            )
        except AlreadyIngested as exc:
            session.commit()
            print(f"skipped: {exc}")
            return 0
        session.commit()

    print(
        f"occurrences parsed : {len(events)}\n"
        f"rows dropped       : {dropped.total} "
        f"(no uid {dropped.no_uid}, all-day/floating {dropped.all_day_or_floating}, "
        f"no end {dropped.no_end}, zero length {dropped.zero_length})\n"
        f"meetings inserted  : {counts.meetings_inserted}\n"
        f"meetings updated   : {counts.meetings_updated}\n"
        f"people created     : {counts.people_created}\n"
        f"attendees written  : {counts.attendees_written}"
    )
    return 0


def _transcript(args: argparse.Namespace) -> int:
    """Store one transcript, and report what it cost in tokens."""
    raw_text = args.path.read_text(encoding="utf-8")
    embedder = get_embedder(args.embedder)
    now = dt.datetime.now(dt.UTC)

    with SessionLocal() as session:
        counts = store_transcript(
            session,
            raw_text=raw_text,
            embedder=embedder,
            now=now,
            source=args.path.suffix.lstrip(".") or "text",
            meeting_external_id=args.meeting,
        )
        session.commit()

    if counts.transcripts_skipped:
        print("skipped: this exact text is already stored (same checksum), nothing re-embedded")
        return 0

    print(
        f"model              : {embedder.name}\n"
        f"chunks written     : {counts.chunks_written}\n"
        f"chunks embedded    : {counts.chunks_embedded}\n"
        f"tokens embedded    : {counts.tokens_embedded}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
