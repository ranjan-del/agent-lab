"""idempotency_keys: remember what a POST answered, so a retry gets the same answer

Revision ID: 0007_idempotency_keys
Revises: 0006_seed_policies
Create Date: 2026-09-30

Module 5 drill. A client that loses the response to POST /ingest/transcripts cannot tell
"it failed" from "it worked and the answer got lost", so it retries. The key it sends makes
that retry safe: the first request stores its status and body here, and a replay returns them.

The primary key is what makes it safe under concurrency, not a SELECT: a second request with
the same key blocks on the first one's uncommitted insert and then sees the finished row.
status_code and response are NULL only inside the transaction that reserved the key, which
nobody else can see; a failed ingest rolls the reservation back with it.

Numbered after 0006. If the w3/policy-engine branch adds a migration, renumber this one and
point down_revision at the new head before merging.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_idempotency_keys"
down_revision: str | None = "0006_seed_policies"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("response", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    # For the reaper that will expire old keys (Stripe keeps them 24 hours). Not built yet.
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
