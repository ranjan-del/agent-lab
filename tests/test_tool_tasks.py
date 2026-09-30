"""Tool 3: write_tasks. Create, update and close task rows. The agent's only writer."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.agent.tools_tasks import NewTask, WriteTasksArgs, write_tasks
from agent_lab.models import Task

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def test_create_writes_one_row_per_task_and_returns_their_ids(session: Session) -> None:
    args = WriteTasksArgs(
        create=[
            NewTask(
                text="Send the demo recording to the client",
                urgency="middle",
                due_at=dt.datetime(2026, 9, 10, 9, tzinfo=IST),
            ),
            NewTask(text="Draft pricing note", urgency="soft"),
        ]
    )

    result = write_tasks(session, args, now=dt.datetime(2026, 9, 7, 12, tzinfo=IST))

    rows = session.execute(select(Task).order_by(Task.id)).scalars().all()
    assert [r.text for r in rows] == ["Send the demo recording to the client", "Draft pricing note"]
    assert [r.urgency for r in rows] == ["middle", "soft"]
    assert all(r.status == "open" and r.agreed_by_me is False for r in rows)
    assert result["created"] == [r.id for r in rows]


def test_update_changes_the_named_fields_and_stamps_updated_at(session: Session) -> None:
    from agent_lab.agent.tools_tasks import TaskUpdate

    now = dt.datetime(2026, 9, 7, 12, tzinfo=IST)
    created = write_tasks(
        session,
        WriteTasksArgs(create=[NewTask(text="Draft pricing note", urgency="soft")]),
        now=now,
    )["created"][0]
    later = now + dt.timedelta(hours=2)

    result = write_tasks(
        session,
        WriteTasksArgs(
            update=[TaskUpdate(id=created, urgency="middle", due_at=later + dt.timedelta(days=2))]
        ),
        now=later,
    )

    row = session.get(Task, created)
    assert result["updated"] == [created]
    assert row.urgency == "middle"
    assert row.due_at == later + dt.timedelta(days=2)
    assert row.text == "Draft pricing note", "fields not named are left alone"
    assert row.updated_at == later


def test_write_tasks_writes_what_it_is_given_and_leaves_deciding_to_the_gate(
    session: Session,
) -> None:
    """The done refusal moved to the policy gate (test_policy_gate.py), the single source of
    truth. The bare writer no longer disagrees with the engine about a confirmed task."""
    from agent_lab.agent.tools_tasks import TaskUpdate

    now = dt.datetime(2026, 9, 7, 12, tzinfo=IST)
    created = write_tasks(
        session, WriteTasksArgs(create=[NewTask(text="Send the recording")]), now=now
    )["created"][0]

    out = write_tasks(
        session, WriteTasksArgs(update=[TaskUpdate(id=created, status="dropped")]), now=now
    )

    assert out == {"created": [], "updated": [created]}
    assert session.get(Task, created).status == "dropped"
