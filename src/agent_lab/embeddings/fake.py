"""A deterministic embedder for tests.

Not a mock. It really produces vectors, they are really normalised, and identical text really
gives an identical vector. What it does not produce is *meaning*: two sentences that mean the
same thing land nowhere near each other.

That trade is deliberate. Tests about storage, batching, chunk boundaries and idempotency want
speed and determinism, and they do not care about semantics. Tests about retrieval quality do
care, and must use the real model. Keeping the two kinds of test honest about which they need
is the reason this class exists.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

DIMENSION = 384


class FakeEmbedder:
    """Hash text into a stable unit vector."""

    @property
    def name(self) -> str:
        return "fake-hash-384"

    @property
    def dimension(self) -> int:
        return DIMENSION

    @property
    def max_tokens(self) -> int:
        return 256

    def count_tokens(self, text: str) -> int:
        """Whitespace words, which is close enough for a test double and needs no tokenizer."""
        return len(text.split())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=DIMENSION).digest()
        # Centre on zero, then normalise, so cosine distance behaves like it does for a real
        # model even though the directions are meaningless.
        raw = [(b - 127.5) / 127.5 for b in digest]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]
