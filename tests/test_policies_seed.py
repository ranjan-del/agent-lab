"""Migration 0006: the thirteen SPEC section 3 rules as rows, against a real Postgres.

The point of the week is that rules are data. These tests prove the data is there, that it
is well-formed, that the tier is constrained by the database, and that the migration can be
undone and redone.
"""

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_lab.agent.policy.rules import load_policies
from agent_lab.models import Policy


def test_thirteen_rules_are_seeded_in_three_tiers(session: Session) -> None:
    by_kind = dict(session.execute(select(Policy.kind, func.count()).group_by(Policy.kind)).all())
    assert by_kind == {"hard": 4, "middle": 5, "soft": 4}


def test_every_seeded_row_loads_through_its_shape(session: Session) -> None:
    """Thirteen rows, thirteen validated parameter objects, no exceptions. A seed row with a
    bad JSONB shape fails here, on the day it is written, not in the engine."""
    loaded = load_policies(session)
    assert len(loaded) == 13
    assert {p.code for p in loaded} == {
        "fixed_meeting_immutable",
        "focus_block",
        "no_external_exposure",
        "never_mark_done",
        "max_meetings_per_day",
        "max_meeting_hours_per_day",
        "min_gap_between_meetings",
        "working_hours",
        "no_change_within_notice",
        "prefer_lightest_day",
        "keep_one_to_ones_same_day",
        "prefer_short_slots",
        "client_calls_late_morning",
    }
    assert all(p.active for p in loaded)


def test_the_focus_block_numbers_live_in_the_row_not_in_code(session: Session) -> None:
    row = session.execute(select(Policy).where(Policy.code == "focus_block")).scalar_one()
    assert row.kind == "hard"
    assert row.rule == {
        "start": "09:00",
        "end": "11:00",
        "days": [1, 2, 3, 4, 5],
        "tz": "Asia/Kolkata",
    }


def test_the_database_refuses_a_fourth_tier(session: Session) -> None:
    """The CHECK is the database's, not Pydantic's: a raw SQL edit hits it too."""
    with pytest.raises(IntegrityError, match="ck_policies_kind"), session.begin_nested():
        session.add(
            Policy(
                code="test_bad_tier", kind="hardish", description="x", rule={}, severity="refuse"
            )
        )
        session.flush()


def test_migration_0006_runs_down_and_back_up(alembic_config, engine) -> None:
    """Up, down, up. The rows are the migration's, so downgrade removes exactly them."""

    def count() -> int:
        with engine.connect() as connection:
            return int(connection.execute(text("SELECT count(*) FROM policies")).scalar_one())

    assert count() == 13
    command.downgrade(alembic_config, "0005_tasks")
    assert count() == 0
    command.upgrade(alembic_config, "head")
    assert count() == 13
