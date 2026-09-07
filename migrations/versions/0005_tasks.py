"""the tasks table, the agent's only persistent output

Revision ID: 0005_tasks
Revises: 0004_meetings_span_gist
Create Date: 2026-09-07

SPEC section 2 decided it: the agent reads the calendar and the transcripts and writes only
here. Urgency reuses the three rule tiers. agreed_by_me carries the hard rule that a task is
never marked done without my confirmation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tasks"
down_revision: str | None = "0004_meetings_span_gist"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("meeting_id", sa.BigInteger(), sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("urgency", sa.String(16), nullable=False, server_default="middle"),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("agreed_by_me", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_by_run_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("urgency IN ('hard', 'middle', 'soft')", name="ck_tasks_urgency"),
        sa.CheckConstraint("status IN ('open', 'done', 'dropped')", name="ck_tasks_status"),
    )
    op.create_index("ix_tasks_status_due_at", "tasks", ["status", "due_at"])
    op.create_index("ix_tasks_meeting_id", "tasks", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_meeting_id", table_name="tasks")
    op.drop_index("ix_tasks_status_due_at", table_name="tasks")
    op.drop_table("tasks")
