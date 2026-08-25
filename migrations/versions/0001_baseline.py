"""baseline: enable the pgvector extension

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-24

The extension has to exist before any column of type `vector` can be created, so it is the
first thing every environment gets. Downgrade drops it, which is why `alembic downgrade base`
is worth running on purpose at least once.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))


def downgrade() -> None:
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
