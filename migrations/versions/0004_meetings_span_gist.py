"""index the meeting span for overlap queries

Revision ID: 0004_meetings_span_gist
Revises: 0003_embedding_dim_384
Create Date: 2026-09-05

"Does anything overlap this window" is the one question the agent asks of the calendar, and a
btree on starts_at cannot answer it: the planner has to read every meeting that STARTS before
the window ends, which is most of the table, and filter on ends_at afterwards. Measured on
100k rows: a sequential scan over 1,661 buffers, ~10 ms, for a window holding 25 meetings.

A GiST index over the half-open range [starts_at, ends_at) answers overlap (&&) and
containment (@>) in one index probe: 1 buffer, ~0.03 ms, same 25 rows.

The index is over an EXPRESSION, so a query only uses it when written with that exact
expression: ``tstzrange(starts_at, ends_at, '[)') && tstzrange(:from, :to, '[)')``. The
two-comparison form ``starts_at < :to AND ends_at > :from`` keeps sequential-scanning. Both
plans are committed under docs/explain/.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_meetings_span_gist"
down_revision: str | None = "0003_embedding_dim_384"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX = "ix_meetings_span_gist"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"CREATE INDEX {_INDEX} ON meetings USING gist (tstzrange(starts_at, ends_at, '[)'))"
        )
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="meetings")
