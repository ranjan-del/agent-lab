"""Module 5: retry with exponential backoff and full jitter, on the embedder seam.

Every test runs on a fake clock and a fake random source, so no test waits and every delay is
an exact number. The last test goes through ``store_transcript`` against Postgres, to show the
retry sits on a real seam and not only on a helper.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_lab.embeddings.base import Embedder
from agent_lab.embeddings.fake import FakeEmbedder
from agent_lab.ingest.transcripts import store_transcript
from agent_lab.models import TranscriptChunk
from agent_lab.retry import Backoff, RetryingEmbedder, TransientError, retry


class FakeClock:
    """Records every sleep and advances its own time. Nothing actually waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def fixed(*values: float):
    """A stand-in for random.random that returns the given values in order."""
    it = iter(values)
    return lambda: next(it)


class FlakyEmbedder(FakeEmbedder):
    """Fails ``failures`` times with ``error``, then behaves like the FakeEmbedder."""

    def __init__(self, failures: int, error: type[Exception] = ConnectionError) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error(f"embedder unavailable (call {self.calls})")
        return super().embed(texts)


def test_a_transient_failure_is_retried_until_it_succeeds_with_jittered_backoff() -> None:
    clock = FakeClock()
    flaky = FlakyEmbedder(failures=2)
    embedder = RetryingEmbedder(
        flaky, Backoff(attempts=4, base_s=0.2, cap_s=5.0), sleep=clock.sleep, rand=fixed(0.5, 0.5)
    )

    vectors = embedder.embed(["hello"])

    assert vectors == FakeEmbedder().embed(["hello"])
    assert flaky.calls == 3
    # full jitter: rand() * min(cap, base * 2**(retry - 1)), so 0.5 * 0.2 then 0.5 * 0.4
    assert clock.sleeps == pytest.approx([0.1, 0.2])
    assert clock.now == pytest.approx(0.3)


def test_it_gives_up_after_the_last_attempt_and_raises_the_real_error() -> None:
    clock = FakeClock()
    flaky = FlakyEmbedder(failures=99)
    embedder = RetryingEmbedder(flaky, Backoff(attempts=3), sleep=clock.sleep, rand=lambda: 1.0)

    with pytest.raises(ConnectionError, match="call 3"):
        embedder.embed(["hello"])

    assert flaky.calls == 3, "bounded: three attempts, not forever"
    assert len(clock.sleeps) == 2, "no pointless sleep after the final failure"


def test_an_error_that_is_not_transient_is_not_retried() -> None:
    clock = FakeClock()
    flaky = FlakyEmbedder(failures=1, error=ValueError)
    embedder = RetryingEmbedder(flaky, Backoff(attempts=5), sleep=clock.sleep)

    with pytest.raises(ValueError):
        embedder.embed(["hello"])

    assert flaky.calls == 1
    assert clock.sleeps == []


def test_the_ceiling_doubles_and_then_stops_at_the_cap() -> None:
    clock = FakeClock()
    calls = 0

    def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise TransientError("still down")

    with pytest.raises(TransientError):
        retry(
            always_fails,
            policy=Backoff(attempts=6, base_s=1.0, cap_s=3.0),
            sleep=clock.sleep,
            rand=lambda: 1.0,  # the top of every jitter range, to expose the ceiling itself
        )

    assert clock.sleeps == [1.0, 2.0, 3.0, 3.0, 3.0]


def test_jitter_spreads_delays_across_the_whole_range() -> None:
    policy = Backoff(base_s=1.0, cap_s=100.0)
    assert policy.delay(3, rand=lambda: 0.0) == 0.0
    assert policy.delay(3, rand=lambda: 0.25) == 1.0
    assert policy.delay(3, rand=lambda: 0.999) == pytest.approx(3.996)


def test_a_policy_with_no_attempts_is_refused() -> None:
    with pytest.raises(ValueError):
        Backoff(attempts=0)


def test_the_wrapper_is_still_an_embedder() -> None:
    wrapped = RetryingEmbedder(FakeEmbedder(), Backoff())
    assert isinstance(wrapped, Embedder)
    assert (wrapped.name, wrapped.dimension, wrapped.max_tokens) == ("fake-hash-384", 384, 256)
    assert wrapped.count_tokens("two words") == 2


def test_store_transcript_survives_a_flaky_embedder_and_embeds_once(session: Session) -> None:
    clock = FakeClock()
    flaky = FlakyEmbedder(failures=2)
    raw = "Anita: can we move the review?\nPriya: Thursday works.\nAnita: Thursday it is."

    counts = store_transcript(
        session,
        raw_text=raw,
        embedder=RetryingEmbedder(flaky, Backoff(attempts=3), sleep=clock.sleep),
        now=dt.datetime(2026, 9, 30, tzinfo=dt.UTC),
    )

    assert counts.transcripts_inserted == 1 and counts.chunks_written >= 1
    assert flaky.calls == 3, "two failures, then one successful batch"
    stored = session.execute(select(func.count()).select_from(TranscriptChunk)).scalar_one()
    assert stored >= counts.chunks_written
    assert len(clock.sleeps) == 2
