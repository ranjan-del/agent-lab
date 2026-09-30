"""Module 5: POST /ingest/transcripts with an Idempotency-Key, against real Postgres.

The claim: a client that retries after a lost response gets the ORIGINAL answer and causes no
second ingest. The response body is what tells the two apart. A genuine second ingest of the
same text would report ``transcripts_skipped: 1`` (the checksum layer); a replay reports the
first call's ``transcripts_inserted: 1``, byte for byte, and the embedder is not called again.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from agent_lab.api.ingest import get_ingest_embedder
from agent_lab.db import get_session
from agent_lab.embeddings.fake import FakeEmbedder
from agent_lab.main import app
from agent_lab.models import IdempotencyKey, Transcript

RAW = "Anita: can we move the review?\nPriya: Thursday works.\nAnita: Thursday it is."


class CountingEmbedder(FakeEmbedder):
    def __init__(self, fail_with: type[Exception] | None = None) -> None:
        self.calls = 0
        self.fail_with = fail_with

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with("embedder broke")
        return super().embed(texts)


@pytest.fixture
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


@pytest.fixture
def api(session: Session, embedder: CountingEmbedder) -> Iterator[TestClient]:
    """The API on the test session, which is rolled back afterwards."""

    def _session() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_ingest_embedder] = lambda: embedder
    yield TestClient(app)
    app.dependency_overrides.clear()


def _transcripts(session: Session) -> int:
    return session.execute(select(func.count()).select_from(Transcript)).scalar_one()


def _post(api: TestClient, key: str | None, raw: str = RAW, source: str = "text"):
    headers = {} if key is None else {"Idempotency-Key": key}
    return api.post("/ingest/transcripts", json={"text": raw, "source": source}, headers=headers)


def test_the_first_request_ingests_and_says_what_it_did(
    api: TestClient, session: Session, embedder: CountingEmbedder
) -> None:
    before = _transcripts(session)

    response = _post(api, "k-first")

    assert response.status_code == 201
    assert response.json()["transcripts_inserted"] == 1
    assert response.json()["chunks_written"] >= 1
    assert "idempotent-replayed" not in response.headers
    assert embedder.calls == 1
    assert _transcripts(session) == before + 1


def test_a_replay_returns_the_original_result_without_ingesting_again(
    api: TestClient, session: Session, embedder: CountingEmbedder
) -> None:
    first = _post(api, "k-replay")
    after_first = _transcripts(session)

    again = _post(api, "k-replay")

    assert again.status_code == first.status_code == 201
    assert again.json() == first.json(), "the original answer, not a fresh one"
    assert again.json()["transcripts_inserted"] == 1, "a re-run would have said skipped"
    assert again.headers["idempotent-replayed"] == "true"
    assert embedder.calls == 1, "nothing was re-embedded"
    assert _transcripts(session) == after_first


def test_the_same_key_with_a_different_body_is_refused(
    api: TestClient, session: Session, embedder: CountingEmbedder
) -> None:
    _post(api, "k-mismatch")
    before = _transcripts(session)

    response = _post(api, "k-mismatch", raw=RAW + "\nPriya: and Friday for the retro.")

    assert response.status_code == 422
    assert "different request" in response.json()["detail"]
    assert embedder.calls == 1
    assert _transcripts(session) == before


def test_a_new_key_for_the_same_text_reaches_the_checksum_layer(
    api: TestClient, embedder: CountingEmbedder
) -> None:
    _post(api, "k-a")

    response = _post(api, "k-b")

    # A new key is a new request. store_transcript still refuses to duplicate the text, and
    # says so, which is the difference a replay hides on purpose.
    assert response.status_code == 200
    assert response.json()["transcripts_skipped"] == 1
    assert embedder.calls == 1


def test_the_key_row_keeps_the_status_and_the_body(api: TestClient, session: Session) -> None:
    response = _post(api, "k-stored")

    row = session.get(IdempotencyKey, "k-stored")
    assert row is not None
    assert row.status_code == 201
    assert row.response == response.json()
    assert len(row.request_hash) == 64 and row.completed_at is not None


@pytest.mark.parametrize("key", [None, ""])
def test_a_missing_key_is_a_400(api: TestClient, key: str | None) -> None:
    response = _post(api, key)
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


def test_a_failed_ingest_releases_the_key_so_a_retry_can_succeed(engine: Engine) -> None:
    """Real commits on purpose: the claim is about what a rollback leaves behind.

    The key is reserved in the same transaction as the ingest, so a failure rolls both back
    and the client's retry with the same key runs for real instead of replaying a failure.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    key = f"k-fail-{uuid.uuid4()}"
    raw = f"Anita: a transcript for {key}.\nPriya: noted."

    def _session() -> Iterator[Session]:
        with factory() as s:
            yield s

    broken = CountingEmbedder(fail_with=ValueError)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_ingest_embedder] = lambda: broken
    try:
        with pytest.raises(ValueError):
            _post(TestClient(app), key, raw=raw)
        with factory() as s:
            assert s.get(IdempotencyKey, key) is None, "the failure did not burn the key"

        app.dependency_overrides[get_ingest_embedder] = CountingEmbedder
        response = _post(TestClient(app), key, raw=raw)
        assert response.status_code == 201
        assert response.json()["transcripts_inserted"] == 1
    finally:
        app.dependency_overrides.clear()
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM idempotency_keys WHERE key = :k"), {"k": key})
            connection.execute(text("DELETE FROM transcripts WHERE raw_text = :r"), {"r": raw})


def test_migration_0007_runs_down_and_back_up(alembic_config, engine: Engine) -> None:
    def exists() -> bool:
        with engine.connect() as connection:
            return bool(
                connection.execute(text("SELECT to_regclass('idempotency_keys')")).scalar_one()
            )

    assert exists()
    command.downgrade(alembic_config, "0006_seed_policies")
    assert not exists()
    command.upgrade(alembic_config, "head")
    assert exists()
