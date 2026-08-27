"""Embedding: turning text into vectors.

Everything goes through :func:`get_embedder`. Nothing else in the codebase imports a specific
model, so swapping models is one environment variable and a migration, not a refactor.
"""

from agent_lab.embeddings.base import Embedder, get_embedder

__all__ = ["Embedder", "get_embedder"]
