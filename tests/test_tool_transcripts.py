"""Tool 2: search_transcripts. Nearest chunks to a query, each with the meeting it came from.

Uses the FakeEmbedder, so this proves plumbing (ranking by distance, the meeting join, the
window filter), not retrieval quality. Quality needs the real model and is a week 3 question.
"""

import datetime as dt

from sqlalchemy.orm import Session

from agent_lab.agent.tools_transcripts import SearchTranscriptsArgs, search_transcripts
from agent_lab.embeddings.fake import FakeEmbedder
from agent_lab.models import Meeting, Transcript, TranscriptChunk

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _transcript(session: Session, title: str, when: dt.datetime, turns: list[str]) -> Transcript:
    meeting = Meeting(
        source="test",
        external_id=f"{title}-{when.isoformat()}",
        title=title,
        starts_at=when,
        ends_at=when + dt.timedelta(hours=1),
        status="confirmed",
        raw={},
        ingested_at=dt.datetime.now(dt.UTC),
    )
    session.add(meeting)
    session.flush()
    transcript = Transcript(
        meeting_id=meeting.id,
        source="test",
        captured_at=when,
        raw_text="\n".join(turns),
        checksum=f"{title}-{when.timestamp()}",
        ingested_at=dt.datetime.now(dt.UTC),
    )
    session.add(transcript)
    session.flush()
    embedder = FakeEmbedder()
    for i, (text, vector) in enumerate(zip(turns, embedder.embed(turns), strict=True)):
        session.add(
            TranscriptChunk(
                transcript_id=transcript.id,
                ordinal=i,
                text=text,
                token_count=embedder.count_tokens(text),
                embedding=vector,
                embedding_model=embedder.name,
            )
        )
    session.flush()
    return transcript


def test_the_exact_chunk_ranks_first_and_carries_its_meeting(session: Session) -> None:
    when = dt.datetime(2026, 9, 3, 11, 0, tzinfo=IST)
    _transcript(
        session,
        "Client demo",
        when,
        ["Ranjan: we will send the recording by Friday", "Client: please include pricing"],
    )

    result = search_transcripts(
        session,
        FakeEmbedder(),
        SearchTranscriptsArgs(query="Client: please include pricing", limit=2),
    )

    assert result["results"][0]["text"] == "Client: please include pricing"
    assert result["results"][0]["meeting"]["title"] == "Client demo"
    assert result["results"][0]["distance"] < 1e-6, "identical text, zero cosine distance"
    assert len(result["results"]) == 2


def test_the_window_filters_out_transcripts_from_other_dates(session: Session) -> None:
    inside = dt.datetime(2026, 9, 3, 11, 0, tzinfo=IST)
    outside = dt.datetime(2026, 8, 20, 11, 0, tzinfo=IST)
    _transcript(session, "This week", inside, ["decided to ship on Monday"])
    _transcript(session, "Last month", outside, ["decided to ship on Monday"])

    result = search_transcripts(
        session,
        FakeEmbedder(),
        SearchTranscriptsArgs(
            query="decided to ship on Monday",
            start=dt.datetime(2026, 9, 1, tzinfo=IST),
            end=dt.datetime(2026, 9, 8, tzinfo=IST),
            limit=5,
        ),
    )

    assert [r["meeting"]["title"] for r in result["results"]] == ["This week"]
