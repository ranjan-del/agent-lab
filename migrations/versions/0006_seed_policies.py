"""seed the thirteen SPEC section 3 rules, and constrain the tier

Revision ID: 0006_seed_policies
Revises: 0005_tasks
Create Date: 2026-09-17

The table has existed since 0002 and has been empty since. SPEC section 3 decided every rule
lives here as a row so that changing a number is a data edit, not a release. This migration
is that data: four hard, five middle, four soft, with the defaults exactly as the SPEC table
gives them. The shapes that validate ``rule`` on read live in ``agent_lab.agent.policy.rules``.

The rows are the migration's own, so downgrade deletes exactly these thirteen codes and
nothing a human added later.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_seed_policies"
down_revision: str | None = "0005_tasks"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_IST_WEEKDAYS = {"days": [1, 2, 3, 4, 5], "tz": "Asia/Kolkata"}

# (code, kind, severity, description, rule)
SEED: list[tuple[str, str, str, str, dict[str, object]]] = [
    # ---- hard: never broken, no override; the agent refuses and names the rule ----
    (
        "fixed_meeting_immutable",
        "hard",
        "refuse",
        "Never move, shorten, or propose moving a meeting marked important or fixed.",
        {"markers": ["important", "fixed"]},
    ),
    (
        "focus_block",
        "hard",
        "refuse",
        "Never place or propose anything inside the focus block.",
        {"start": "09:00", "end": "11:00", **_IST_WEEKDAYS},
    ),
    (
        "no_external_exposure",
        "hard",
        "refuse",
        "Never add an external person to an internal meeting; "
        "never let transcript content leave the database.",
        {},
    ),
    (
        "never_mark_done",
        "hard",
        "refuse",
        "Never mark a task done that I did not confirm.",
        {},
    ),
    # ---- middle: hold in a normal week; broken only for something top-urgent, after asking ----
    (
        "max_meetings_per_day",
        "middle",
        "ask",
        "No more than this many meetings in one day.",
        {"max": 6},
    ),
    (
        "max_meeting_hours_per_day",
        "middle",
        "ask",
        "No more than this many hours of meetings in one day.",
        {"max_hours": 4},
    ),
    (
        "min_gap_between_meetings",
        "middle",
        "ask",
        "No two meetings without a gap between them.",
        {"minutes": 15},
    ),
    (
        "working_hours",
        "middle",
        "ask",
        "Nothing outside working hours or on weekends.",
        {"start": "10:00", "end": "19:00", **_IST_WEEKDAYS},
    ),
    (
        "no_change_within_notice",
        "middle",
        "ask",
        "No proposing a change to a meeting starting within the notice period.",
        {"hours": 24},
    ),
    # ---- soft: preferences the agent may set aside on its own, as long as it says so ----
    (
        "prefer_lightest_day",
        "soft",
        "advise",
        "Prefer the lightest days when suggesting slots; "
        "spread new meetings rather than stack them.",
        {},
    ),
    (
        "keep_one_to_ones_same_day",
        "soft",
        "advise",
        "Keep one-to-ones on the same day when possible.",
        {},
    ),
    (
        "prefer_short_slots",
        "soft",
        "advise",
        "Prefer slots of this length over longer ones.",
        {"minutes": 30},
    ),
    (
        "client_calls_late_morning",
        "soft",
        "advise",
        "Prefer mornings after the focus block for client calls.",
        {"start": "11:00", "end": "13:00", **_IST_WEEKDAYS},
    ),
]

_policies = sa.table(
    "policies",
    sa.column("code", sa.String),
    sa.column("kind", sa.String),
    sa.column("severity", sa.String),
    sa.column("description", sa.Text),
    sa.column("rule", postgresql.JSONB),
    sa.column("active", sa.Boolean),
)


def upgrade() -> None:
    op.create_check_constraint("ck_policies_kind", "policies", "kind IN ('hard', 'middle', 'soft')")
    op.create_check_constraint(
        "ck_policies_severity", "policies", "severity IN ('refuse', 'ask', 'advise')"
    )
    op.bulk_insert(
        _policies,
        [
            {
                "code": code,
                "kind": kind,
                "severity": severity,
                "description": description,
                "rule": rule,
                "active": True,
            }
            for code, kind, severity, description, rule in SEED
        ],
    )


def downgrade() -> None:
    op.execute(_policies.delete().where(_policies.c.code.in_([code for code, *_ in SEED])))
    op.drop_constraint("ck_policies_severity", "policies", type_="check")
    op.drop_constraint("ck_policies_kind", "policies", type_="check")
