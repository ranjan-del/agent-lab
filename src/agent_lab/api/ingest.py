"""``POST /ingest/transcripts``: store a transcript, safely retryable with an Idempotency-Key.

Two layers, doing different jobs:

* **The checksum** (``store_transcript``) stops the same text being stored twice, whoever
  sends it and however often. It makes a repeat *harmless*.
* **The key** makes a repeat *invisible*: the retry gets the first request's status and body,
  byte for byte, and no work runs. Without it a client whose first response was lost would
  get "skipped" the second time and could not tell whether its own request had worked.

The key is reserved with ``INSERT ... ON CONFLICT DO NOTHING`` in the same transaction as the
ingest. A concurrent duplicate blocks on that insert until the first commits, then replays;
a failed ingest rolls the reservation back, so the retry runs for real.

Same key with a different body is a client bug, and is refused with 422 rather than
answered with a result for a request the client did not send.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agent_lab.db import get_session
from agent_lab.embeddings import get_embedder
from agent_lab.embeddings.base import Embedder
from agent_lab.ingest.transcripts import store_transcript
from agent_lab.models import IdempotencyKey
from agent_lab.retry import Backoff, RetryingEmbedder

router = APIRouter()

REPLAYED = "Idempotent-Replayed"


class TranscriptIn(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field(default="text", max_length=32)
    meeting_external_id: str | None = None


def get_ingest_embedder() -> Embedder:
    return RetryingEmbedder(get_embedder(), Backoff())


def request_hash(body: TranscriptIn) -> str:
    canonical = json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("/ingest/transcripts", status_code=status.HTTP_201_CREATED)
def ingest_transcript(
    body: TranscriptIn,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_ingest_embedder)],
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> dict[str, Any]:
    """Store one transcript. Replaying the same Idempotency-Key replays the first answer."""
    if not idempotency_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Idempotency-Key header is required")
    digest = request_hash(body)
    now = dt.datetime.now(dt.UTC)

    reserved = session.execute(
        insert(IdempotencyKey)
        .values(key=idempotency_key, request_hash=digest, created_at=now)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(IdempotencyKey.key)
    ).scalar_one_or_none()

    if reserved is None:
        prior = session.execute(
            select(IdempotencyKey).where(IdempotencyKey.key == idempotency_key)
        ).scalar_one()
        if prior.request_hash != digest:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "this Idempotency-Key was already used for a different request body",
            )
        response.status_code = prior.status_code or status.HTTP_200_OK
        response.headers[REPLAYED] = "true"
        return prior.response or {}

    counts = store_transcript(
        session,
        raw_text=body.text,
        embedder=embedder,
        now=now,
        source=body.source,
        meeting_external_id=body.meeting_external_id,
    )
    result: dict[str, Any] = asdict(counts)
    code = status.HTTP_201_CREATED if counts.transcripts_inserted else status.HTTP_200_OK
    row = session.get(IdempotencyKey, idempotency_key)
    assert row is not None  # reserved above, in this transaction
    row.status_code = code
    row.response = result
    row.completed_at = now
    session.commit()
    response.status_code = code
    return result
