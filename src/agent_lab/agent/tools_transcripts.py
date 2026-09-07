"""Tool 2: search the transcripts for a query, returning the nearest passages.

Vector search only in v0.1, over the HNSW index. Each hit carries the meeting it came from,
because a passage without its meeting cannot become a task. Hybrid retrieval (adding the
lexical half via the tsvector column) is week 3's fundamentals topic and lands then.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.embeddings.base import Embedder
from agent_lab.models import Meeting, Transcript, TranscriptChunk

DEFAULT_TZ = "Asia/Kolkata"


class SearchTranscriptsArgs(BaseModel):
    query: str = Field(min_length=1, description="What to look for: a topic, a promise, a name.")
    start: dt.datetime | None = Field(
        default=None, description="Only transcripts captured at or after this instant."
    )
    end: dt.datetime | None = Field(
        default=None, description="Only transcripts captured before this instant."
    )
    limit: int = Field(default=5, ge=1, le=20)
    timezone: str = DEFAULT_TZ

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, value: dt.datetime | None) -> dt.datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("must be timezone-aware, e.g. 2026-09-01T00:00:00+05:30")
        return value


def search_transcripts(
    session: Session, embedder: Embedder, args: SearchTranscriptsArgs
) -> dict[str, Any]:
    tz = ZoneInfo(args.timezone)
    [query_vector] = embedder.embed([args.query])
    distance = TranscriptChunk.embedding.cosine_distance(query_vector)

    stmt = (
        select(TranscriptChunk, Transcript, Meeting, distance.label("distance"))
        .join(Transcript, Transcript.id == TranscriptChunk.transcript_id)
        .outerjoin(Meeting, Meeting.id == Transcript.meeting_id)
        .where(TranscriptChunk.embedding.is_not(None))
        .where(TranscriptChunk.embedding_model == embedder.name)
    )
    if args.start is not None:
        stmt = stmt.where(Transcript.captured_at >= args.start)
    if args.end is not None:
        stmt = stmt.where(Transcript.captured_at < args.end)
    stmt = stmt.order_by(distance).limit(args.limit)

    results: list[dict[str, Any]] = []
    for chunk, transcript, meeting, dist in session.execute(stmt):
        results.append(
            {
                "text": chunk.text,
                "distance": float(dist),
                "captured_at": transcript.captured_at.astimezone(tz).isoformat(),
                "meeting": None
                if meeting is None
                else {
                    "id": meeting.id,
                    "title": meeting.title,
                    "start": meeting.starts_at.astimezone(tz).isoformat(),
                },
            }
        )
    return {"query": args.query, "embedding_model": embedder.name, "results": results}
