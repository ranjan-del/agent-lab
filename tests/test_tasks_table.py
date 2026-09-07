"""The tasks table: the agent's only persistent output (SPEC section 2).

Against a real Postgres, because the constraints are the point.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.models import Task


def test_a_task_round_trips_with_its_tier_and_source_meeting(session: Session) -> None:
    task = Task(
        text="Send the demo recording to the client",
        urgency="middle",
        due_at=dt.datetime(2026, 9, 10, 9, 0, tzinfo=dt.UTC),
        status="open",
        agreed_by_me=True,
        created_at=dt.datetime(2026, 9, 7, 10, 0, tzinfo=dt.UTC),
    )
    session.add(task)
    session.flush()

    stored = session.execute(select(Task).where(Task.id == task.id)).scalar_one()
    assert stored.urgency == "middle"
    assert stored.status == "open"
    assert stored.meeting_id is None, "a task may exist without a source meeting"
    assert stored.created_by_run_id is None, "and before any agent run has written it"
