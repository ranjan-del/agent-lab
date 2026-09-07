"""Tool 3: write the task list. The agent's only writer, and fully reversible (SPEC section 2).

Create, update and close rows in ``tasks``. Nothing here leaves the database. One rule is
enforced right here rather than waiting for the policy engine, because it is a hard rule with
no override: the agent never marks a task done. Only I do.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent_lab.models import Task

Urgency = Literal["hard", "middle", "soft"]


class NewTask(BaseModel):
    text: str = Field(min_length=1, description="What I agreed to do, in one sentence.")
    urgency: Urgency = "middle"
    due_at: dt.datetime | None = None
    meeting_id: int | None = Field(
        default=None, description="The meeting this came from, if known."
    )


class TaskUpdate(BaseModel):
    """Only the fields given are changed. ``status`` may be 'open' or 'dropped'; never 'done'."""

    id: int
    text: str | None = Field(default=None, min_length=1)
    urgency: Urgency | None = None
    due_at: dt.datetime | None = None
    status: Literal["open", "dropped", "done"] | None = None


class WriteTasksArgs(BaseModel):
    create: list[NewTask] = Field(default_factory=list)
    update: list[TaskUpdate] = Field(default_factory=list)


NEVER_DONE = (
    "hard rule: never mark a task done that I did not confirm. "
    "Leave status alone, or set it to 'dropped' if the task no longer applies."
)


def write_tasks(
    session: Session,
    args: WriteTasksArgs,
    *,
    now: dt.datetime,
    run_id: int | None = None,
) -> dict[str, Any]:
    # Refuse before writing anything, so a rejected batch leaves no half-applied rows.
    if any(u.status == "done" for u in args.update):
        return {"error": NEVER_DONE, "created": [], "updated": []}

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
