"""narrow the embedding column to 384 dimensions

Revision ID: 0003_embedding_dim_384
Revises: 0002_domain_schema
Create Date: 2026-08-27

The original 1536 was a placeholder for a model that was never chosen. The chosen model is
all-MiniLM-L6-v2, which emits 384 dimensions.

The HNSW index has to be dropped first: it is built over the column's vectors, so the column
type cannot change underneath it.

This migration DISCARDS any stored vectors, and says so out loud rather than pretending
otherwise. Vectors from one model cannot be compared with vectors from another, so a
dimension change always implies a full re-embed. Doing it as an explicit NULL is honest; a
cast that silently produced garbage would be worse.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0003_embedding_dim_384"
down_revision: str | None = "0002_domain_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_HNSW = "CREATE INDEX ix_chunks_embedding_hnsw ON transcript_chunks USING hnsw (embedding {ops})"


def upgrade() -> None:
    op.drop_index("ix_chunks_embedding_hnsw", table_name="transcript_chunks")
    op.execute(sa.text("UPDATE transcript_chunks SET embedding = NULL, embedding_model = NULL"))
    op.alter_column(
        "transcript_chunks",
        "embedding",
        type_=pgvector.sqlalchemy.Vector(384),
        existing_type=pgvector.sqlalchemy.Vector(1536),
        existing_nullable=True,
        postgresql_using="NULL",
    )
    op.execute(sa.text(_HNSW.format(ops="vector_cosine_ops")))


def downgrade() -> None:
    op.drop_index("ix_chunks_embedding_hnsw", table_name="transcript_chunks")
    op.execute(sa.text("UPDATE transcript_chunks SET embedding = NULL, embedding_model = NULL"))
    op.alter_column(
        "transcript_chunks",
        "embedding",
        type_=pgvector.sqlalchemy.Vector(1536),
        existing_type=pgvector.sqlalchemy.Vector(384),
        existing_nullable=True,
        postgresql_using="NULL",
    )
    op.execute(sa.text(_HNSW.format(ops="vector_cosine_ops")))
