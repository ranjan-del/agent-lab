"""Tool 3: write the task list. The agent's only writer, and fully reversible (SPEC section 2).

Create, update and close rows in ``tasks``. Nothing here leaves the database.

This function writes what it is given and decides nothing. The policy gate (``gate.py``) is
the single place a write is allowed, refused or held, and ``execute()`` only ever reaches
this function through it. That includes "never mark a task done that I did not confirm",
which used to be refused here as well, with a different answer from the engine's.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from agent_lab.models import Task

Urgency = Literal["hard", "middle", "soft"]


class NewTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="What I agreed to do, in one sentence.")
    urgency: Urgency = "middle"
    due_at: dt.datetime | None = None
    meeting_id: int | None = Field(
        default=None, description="The meeting this came from, if known."
    )


class TaskUpdate(BaseModel):
    """Only the fields given are changed. 'done' passes the gate only for a task I confirmed."""

    model_config = ConfigDict(extra="forbid")

    id: int
    text: str | None = Field(default=None, min_length=1)
    urgency: Urgency | None = None
    due_at: dt.datetime | None = None
    status: Literal["open", "dropped", "done"] | None = None


class WriteTasksArgs(BaseModel):
    """Unknown keys are an error: ``creates`` for ``create`` must not validate as nothing."""

    model_config = ConfigDict(extra="forbid")

    create: list[NewTask] = Field(default_factory=list)
    update: list[TaskUpdate] = Field(default_factory=list)


def write_tasks(
    session: Session,
    args: WriteTasksArgs,
    *,
    now: dt.datetime,
    run_id: int | None = None,
) -> dict[str, Any]:
    created: list[int] = []
    for item in args.create:
        task = Task(
            text=item.text,
            urgency=item.urgency,
            due_at=item.due_at,
            meeting_id=item.meeting_id,
            status="open",
            agreed_by_me=False,
            created_by_run_id=run_id,
            created_at=now,
        )
        session.add(task)
        session.flush()
        created.append(task.id)

    updated: list[int] = []
    for change in args.update:
        existing = session.get(Task, change.id)
        if existing is None:
            return {"error": f"no task with id {change.id}", "created": created, "updated": updated}
        for field_name in ("text", "urgency", "due_at", "status"):
            value = getattr(change, field_name)
            if value is not None:
                setattr(existing, field_name, value)
        existing.updated_at = now
        session.flush()
        updated.append(existing.id)
    return {"created": created, "updated": updated}
