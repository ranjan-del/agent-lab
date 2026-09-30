"""The domain schema.

Design rules applied throughout:

* Every timestamp is timezone-aware (``TIMESTAMPTZ``). Storing local time is a bug that only
  shows up twice a year, at a DST boundary, in someone else's timezone.
* Uniqueness is what makes ingestion idempotent. Re-importing a calendar must change nothing,
  and the guarantee lives in the database rather than in a Python ``if`` that can be skipped.
* The original payload is kept in ``raw``. When the parser turns out to be wrong, you re-parse
  instead of re-fetching.
* Recurring events are stored as expanded occurrences: one row per occurrence, linked by a
  nullable ``series_id``. Querying "what is on Tuesday" is then a single WHERE clause.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# One embedding model, one dimension.
#
# 384 is all-MiniLM-L6-v2's output size. pgvector fixes the dimension on the column, so
# changing the model is a migration AND a full re-embed of every stored chunk: vectors from
# two different models are not comparable, and mixing them silently degrades retrieval rather
# than failing. That is why the number lives here, once, and why `embedding_model` is stored
# on every row.
EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    """Declarative base every model inherits from."""


class People(Base):
    """One human or meeting resource, deduplicated across every calendar event.

    A room booked as an attendee is a resource, not a person, and must never trigger a
    double-booking violation. That distinction is why ``is_resource`` exists.
    """

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    # Read by the policy engine in W3: {"mon": [["13:00","16:00"]], ...}
    focus_hours: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_resource: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_trainee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attendees: Mapped[list[Attendee]] = relationship(back_populates="person")


class Meeting(Base):
    """One *occurrence* of a calendar event.

    A weekly standup is many rows, not one row plus a rule. ``series_id`` links them so the
    series can still be reasoned about, and ``is_exception`` marks an occurrence that was
    edited or moved away from its series pattern.
    """

    __tablename__ = "meetings"
    __table_args__ = (
        # Re-importing the same calendar must change nothing. This constraint is that promise.
        UniqueConstraint("source", "external_id", name="uq_meetings_source_external_id"),
        # A meeting that ends before it starts would silently pass every policy check.
        CheckConstraint("ends_at > starts_at", name="ck_meetings_ends_after_starts"),
        Index("ix_meetings_starts_at", "starts_at"),
        Index("ix_meetings_series_id", "series_id"),
        # Overlap and containment in one probe. Only used by queries written with the same
        # range expression; see migrations/versions/0004_meetings_span_gist.py.
        Index(
            "ix_meetings_span_gist",
            text("tstzrange(starts_at, ends_at, '[)')"),
            postgresql_using="gist",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    series_id: Mapped[str | None] = mapped_column(String(512))
    is_exception: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    title: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    organizer_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    is_external: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="confirmed")
    # The untouched payload, so a parser bug is re-parsed rather than re-fetched.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attendees: Mapped[list[Attendee]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )
    transcripts: Mapped[list[Transcript]] = relationship(back_populates="meeting")


class Attendee(Base):
    """One person's participation in one meeting.

    A person attends many meetings and a meeting has many people, so neither table can hold
    the other. Every column here describes the *pairing*, not the person and not the meeting.
    """

    __tablename__ = "attendees"
    __table_args__ = (
        UniqueConstraint("meeting_id", "person_id", name="uq_attendees_meeting_person"),
        Index("ix_attendees_person_id", "person_id"),
        Index("ix_attendees_meeting_id", "meeting_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    response_status: Mapped[str] = mapped_column(String(32), nullable=False, default="needsAction")
    is_optional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_organizer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    meeting: Mapped[Meeting] = relationship(back_populates="attendees")
    person: Mapped[People] = relationship(back_populates="attendees")


class Transcript(Base):
    """One transcript document.

    ``meeting_id`` is nullable on purpose: a transcript does not always match a calendar
    event, and dropping those would lose real data. ``checksum`` over the raw text is what
    stops the same transcript being stored twice under two different filenames.
    """

    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("checksum", name="uq_transcripts_checksum"),
        Index("ix_transcripts_meeting_id", "meeting_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    meeting: Mapped[Meeting | None] = relationship(back_populates="transcripts")
    chunks: Mapped[list[TranscriptChunk]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )


class TranscriptChunk(Base):
    """One retrievable passage, with both halves of week 8's hybrid retrieval on it.

    ``tsv`` is a generated column, so Postgres maintains it and it can never drift out of sync
    with ``text``. ``embedding`` is maintained by the application, because only the application
    knows which model produced it.
    """

    __tablename__ = "transcript_chunks"
    __table_args__ = (
        UniqueConstraint("transcript_id", "ordinal", name="uq_chunks_transcript_ordinal"),
        Index("ix_chunks_transcript_id", "transcript_id"),
        # GIN over the generated tsvector: the lexical half.
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        # HNSW over the embedding: the dense half. Chosen over IVFFlat because it gives
        # better recall without needing a training step, at the cost of more memory.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transcript_id: Mapped[int] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    transcript: Mapped[Transcript] = relationship(back_populates="chunks")


class IngestionRun(Base):
    """One import job.

    Exists so that "did this already run?" is a database question rather than a guess, and so
    a failed import leaves a row saying what broke instead of leaving silence.
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ingestion_runs_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(Text)


class Policy(Base):
    """One rule the agent must obey. Empty until week 3.

    ``rule`` is JSONB so a new rule kind needs no migration during the week the rules change
    most. The database cannot validate that shape, so Pydantic models validate on read, and
    those validators need their own tests. That trade is deliberate.
    """

    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("code", name="uq_policies_code"),
        CheckConstraint("kind IN ('hard', 'middle', 'soft')", name="ck_policies_kind"),
        CheckConstraint("severity IN ('refuse', 'ask', 'advise')", name="ck_policies_severity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="refuse")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(Base):
    """One agent invocation. Empty until week 2.

    ``refusal_reason`` and ``policy_id`` are the columns the whole project exists to fill: an
    agent that refuses, and can name the rule it refused under.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_started_at", "started_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32))
    refusal_reason: Mapped[str | None] = mapped_column(Text)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id", ondelete="SET NULL"))
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    steps: Mapped[list[RunStep]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RunStep(Base):
    """One action inside a run, in order. Empty until week 2.

    Every field week 9 tracing and week 10 costing needs is here from the start, because
    adding them later means the early runs have no numbers and cannot be compared.
    """

    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_run_steps_run_ordinal"),
        Index("ix_run_steps_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(128))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class Task(Base):
    """One thing I agreed to do, extracted from a meeting. The agent's only persistent output.

    SPEC section 2: fully reversible, nothing leaves the database. ``urgency`` reuses the three
    rule tiers (hard, middle, soft) so "what may the agent do about this" reads the same way
    everywhere. ``agreed_by_me`` is the hard rule "never mark a task done that I did not
    confirm" made into a column.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("urgency IN ('hard', 'middle', 'soft')", name="ck_tasks_urgency"),
        CheckConstraint("status IN ('open', 'done', 'dropped')", name="ck_tasks_status"),
        Index("ix_tasks_status_due_at", "status", "due_at"),
        Index("ix_tasks_meeting_id", "meeting_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False, default="middle")
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    agreed_by_me: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
