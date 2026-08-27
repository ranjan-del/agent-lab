"""The seam every embedder sits behind.

A ``Protocol`` rather than a base class, so an implementation only has to have the right shape.
Nothing has to inherit from anything, and a test double is a plain object.

Why a seam at all: the embedding model is the single most likely thing in this system to
change. Model choice affects cost, quality, latency, the column's dimension and whether the
thing runs offline. Pinning that choice in one place means changing it costs a migration and a
re-embed, not an afternoon of grep.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Turn text into vectors.

    ``embed`` takes a batch and not a single string on purpose: every real backend, local or
    remote, is dramatically faster per item on batches, and an interface that only offers one
    at a time quietly forces the slow path everywhere.
    """

    @property
    def name(self) -> str:
        """Stable identifier stored on every row, so you can tell which model made a vector."""

    @property
    def dimension(self) -> int:
        """Output size. Must match the pgvector column, or inserts fail."""

    @property
    def max_tokens(self) -> int:
        """The model's input ceiling. Longer input is TRUNCATED, usually in silence."""

    def count_tokens(self, text: str) -> int:
        """Tokens this model would use. The chunker's budget depends on it being exact."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors, in the same order as the inputs."""


def get_embedder(name: str | None = None) -> Embedder:
    """Return the configured embedder.

    Defaults to the local model, so the project runs, tests and evaluates with no API key, no
    network and no bill. The whole eval loop being free is what makes it affordable to run
    sixty cases on every change.
    """
    choice = (name or os.environ.get("AGENT_LAB_EMBEDDER") or "local").lower()

    if choice == "local":
        from agent_lab.embeddings.local import LocalEmbedder

        return LocalEmbedder()
    if choice == "fake":
        from agent_lab.embeddings.fake import FakeEmbedder

        return FakeEmbedder()
    raise ValueError(f"unknown embedder {choice!r}; expected 'local' or 'fake'")
