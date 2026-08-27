"""all-MiniLM-L6-v2, running on this machine.

Free, offline, no API key, and therefore affordable to run on every eval case on every change.
That last property is what makes a sixty-case eval suite practical rather than a monthly event.

The trap this class exists to close: **the model silently truncates input beyond 256 tokens.**
It does not warn, it does not error, it returns a perfectly valid vector for the first part of
your text and discards the rest. A chunk that is too long therefore embeds as something other
than what it says, and retrieval quietly gets worse for reasons nothing reports. Exposing
``max_tokens`` and an exact ``count_tokens`` is how the chunker avoids ever handing it one.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cached_property
from typing import Any

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIMENSION = 384

_MISSING = (
    "sentence-transformers is not installed. It lives in an optional group so the API image "
    "and CI stay small. Install it with:  uv sync --group embed"
)


class LocalEmbedder:
    """Embed with a sentence-transformers model held in memory."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._model_name = model_name

    @cached_property
    def _model(self) -> Any:
        """Load lazily, and once.

        Lazily because importing torch costs seconds and megabytes, and most code paths, the
        API included, never embed anything. Once because loading per call would dominate the
        runtime of any real batch.
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - exercised by the install path
            raise RuntimeError(_MISSING) from exc
        return SentenceTransformer(self._model_name)

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return DIMENSION

    @property
    def max_tokens(self) -> int:
        """The real ceiling, read from the loaded model rather than hard-coded."""
        return int(self._model.max_seq_length)

    def count_tokens(self, text: str) -> int:
        """Count with the model's OWN tokenizer.

        Estimating by word count is off by enough to matter: technical text, names and
        punctuation all tokenize into more pieces than they look like. An estimate that runs
        under the true count is how a chunk gets truncated despite a budget check passing.
        """
        return len(self._model.tokenizer.encode(text, add_special_tokens=True))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=32,
            # Normalised at source, so cosine distance is a dot product and pgvector's
            # vector_cosine_ops behaves consistently with anything computed in Python.
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(v) for v in row] for row in vectors]
