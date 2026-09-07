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


def test_the_agent_may_drop_a_task_but_never_mark_it_done(session: Session) -> None:
    """SPEC section 3, hard tier: never mark a task done that I did not confirm."""
    from agent_lab.agent.tools_tasks import TaskUpdate

    now = dt.datetime(2026, 9, 7, 12, tzinfo=IST)
    created = write_tasks(
        session, WriteTasksArgs(create=[NewTask(text="Send the recording")]), now=now
    )["created"][0]

    refused = write_tasks(
        session, WriteTasksArgs(update=[TaskUpdate(id=created, status="done")]), now=now
    )
    assert session.get(Task, created).status == "open"
    assert "error" in refused and "never mark a task done" in refused["error"]

    dropped = write_tasks(
        session, WriteTasksArgs(update=[TaskUpdate(id=created, status="dropped")]), now=now
    )
    assert dropped["updated"] == [created]
    assert session.get(Task, created).status == "dropped"
