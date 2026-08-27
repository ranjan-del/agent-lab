"""Store a transcript: parse into turns, chunk, embed, write.

Idempotency works differently here than for calendars. A calendar event has a stable external
id from its source; a transcript file usually has nothing but its content. So identity is a
**checksum of the raw text**: the same transcript under three filenames is one row, and an
edited transcript is genuinely a different one.

Embedding is the expensive step, in money and in time, so it happens once per chunk, in one
batch, after everything else is decided. Re-importing an unchanged transcript re-embeds
nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agent_lab.embeddings.base import Embedder
from agent_lab.ingest.chunking import chunk_turns, parse_turns
from agent_lab.models import Meeting, Transcript, TranscriptChunk


@dataclass(slots=True)
class TranscriptCounts:
    """What one transcript import did."""

    transcripts_inserted: int = 0
    transcripts_skipped: int = 0
    chunks_written: int = 0
    chunks_embedded: int = 0
    tokens_embedded: int = 0

    @property
    def rows_written(self) -> int:
        return self.transcripts_inserted + self.chunks_written


def checksum(raw_text: str) -> str:
    """Identity for a transcript.

    Over the raw bytes, not the parsed turns: a change to the parser must not silently make an
    already-stored transcript look new and duplicate it.
    """
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def store_transcript(
    session: Session,
    *,
    raw_text: str,
    embedder: Embedder,
    now: dt.datetime,
    source: str = "text",
    language: str = "en",
    captured_at: dt.datetime | None = None,
    meeting_external_id: str | None = None,
    overlap_turns: int = 1,
) -> TranscriptCounts:
    """Store one transcript and its chunks. Safe to call repeatedly with the same text."""
    counts = TranscriptCounts()
    digest = checksum(raw_text)

    existing = session.execute(
        select(Transcript).where(Transcript.checksum == digest)
    ).scalar_one_or_none()
    if existing is not None:
        # Unchanged text means unchanged chunks means unchanged vectors. Re-embedding here
        # would be the single most expensive no-op in the system.
        counts.transcripts_skipped += 1
        return counts

    meeting_id: int | None = None
    if meeting_external_id is not None:
        meeting_id = session.execute(
            select(Meeting.id).where(Meeting.external_id == meeting_external_id)
        ).scalar_one_or_none()

    transcript = Transcript(
        meeting_id=meeting_id,
        source=source,
        captured_at=captured_at or now,
        language=language,
        raw_text=raw_text,
        checksum=digest,
        ingested_at=now,
    )
    session.add(transcript)
    session.flush()
    counts.transcripts_inserted += 1

    chunks = chunk_turns(
        parse_turns(raw_text),
        count_tokens=embedder.count_tokens,
        max_tokens=embedder.max_tokens,
        overlap_turns=overlap_turns,
    )
    if not chunks:
        return counts

    # One batch, not one call per chunk. Every backend is dramatically faster per item on a
    # batch, and a remote one is also billed per request.
    vectors = embedder.embed([c.text for c in chunks])

    for chunk, vector in zip(chunks, vectors, strict=True):
        session.execute(
            insert(TranscriptChunk)
            .values(
                transcript_id=transcript.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                token_count=chunk.token_count,
                embedding=vector,
                embedding_model=embedder.name,
            )
            .on_conflict_do_update(
                index_elements=["transcript_id", "ordinal"],
                set_={
                    "text": chunk.text,
                    "token_count": chunk.token_count,
                    "embedding": vector,
                    "embedding_model": embedder.name,
                },
            )
        )
        counts.chunks_written += 1
        counts.chunks_embedded += 1
        counts.tokens_embedded += chunk.token_count

    return counts


def reembed_transcript(
    session: Session, *, transcript_id: int, embedder: Embedder
) -> TranscriptCounts:
    """Re-chunk and re-embed one stored transcript.

    Needed whenever the model or the chunking strategy changes. Vectors from two models are not
    comparable, so a mixed table degrades retrieval silently rather than failing, which is worse
    than either alternative. The old chunks are deleted rather than updated because the new
    chunking may produce a different number of them.
    """
    transcript = session.execute(
        select(Transcript).where(Transcript.id == transcript_id)
    ).scalar_one()
    session.execute(delete(TranscriptChunk).where(TranscriptChunk.transcript_id == transcript_id))
    session.flush()

    counts = TranscriptCounts()
    chunks = chunk_turns(
        parse_turns(transcript.raw_text),
        count_tokens=embedder.count_tokens,
        max_tokens=embedder.max_tokens,
    )
    if not chunks:
        return counts

    vectors = embedder.embed([c.text for c in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        session.add(
            TranscriptChunk(
                transcript_id=transcript_id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                token_count=chunk.token_count,
                embedding=vector,
                embedding_model=embedder.name,
            )
        )
        counts.chunks_written += 1
        counts.chunks_embedded += 1
        counts.tokens_embedded += chunk.token_count
    return counts
