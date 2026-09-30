"""Retry a flaky call: bounded attempts, exponential backoff, full jitter.

The three choices, and what each one prevents:

* **Bounded.** A retry loop without a limit turns an outage into a hung request. After
  ``attempts`` tries the real error is raised, not a wrapper, so the caller sees what broke.
* **Exponential.** The ceiling doubles per retry up to ``cap_s``: quick retries for a blip,
  patience for an outage, and a hard upper bound on any one wait.
* **Full jitter.** Each wait is uniform in ``[0, ceiling]``. Without it, every client that
  failed together retries together, and the dependency meets the same spike again on each
  round. Jitter spreads the herd out; "full" jitter spreads it most for the least total wait.

Only errors that can plausibly succeed on a second try are retried. A ``ValueError`` from bad
input fails the same way every time, and retrying it only delays the report.

``sleep`` and ``rand`` are parameters so tests run on a fake clock and never wait.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agent_lab.embeddings.base import Embedder

log = logging.getLogger(__name__)


class TransientError(Exception):
    """A failure the dependency may not repeat: rate limit, timeout, connection reset."""


RETRYABLE: tuple[type[BaseException], ...] = (TransientError, ConnectionError, TimeoutError)


@dataclass(frozen=True)
class Backoff:
    attempts: int = 4
    base_s: float = 0.2
    cap_s: float = 5.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1; one attempt means no retries")

    def delay(self, retry: int, rand: Callable[[], float] = random.random) -> float:
        """Seconds before retry number ``retry`` (1 for the first retry)."""
        ceiling = min(self.cap_s, self.base_s * 2.0 ** (retry - 1))
        return rand() * ceiling


def retry[T](
    fn: Callable[[], T],
    *,
    policy: Backoff,
    retry_on: tuple[type[BaseException], ...] = RETRYABLE,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    for attempt in range(1, policy.attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt == policy.attempts:
                raise
            wait = policy.delay(attempt, rand)
            log.warning(
                "attempt %d/%d failed (%s: %s); retrying in %.3fs",
                attempt,
                policy.attempts,
                type(exc).__name__,
                exc,
                wait,
            )
            sleep(wait)
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


class RetryingEmbedder:
    """An Embedder whose ``embed`` is retried. Everything else passes straight through.

    The embedder is the seam because it is the call most likely to fail transiently: today a
    local model, and the day it becomes a hosted API, a network call with rate limits.
    Wrapping the seam, not ``store_transcript``, retries the flaky call alone and not the
    database writes around it.
    """

    def __init__(
        self,
        inner: Embedder,
        policy: Backoff,
        *,
        retry_on: tuple[type[BaseException], ...] = RETRYABLE,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._retry_on = retry_on
        self._sleep = sleep
        self._rand = rand

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    @property
    def max_tokens(self) -> int:
        return self._inner.max_tokens

    def count_tokens(self, text: str) -> int:
        return self._inner.count_tokens(text)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return retry(
            lambda: self._inner.embed(texts),
            policy=self._policy,
            retry_on=self._retry_on,
            sleep=self._sleep,
            rand=self._rand,
        )
