"""The write gate: each write_tasks item is decided against the rows before it is written.

Against a real Postgres and the seeded rows, because the gate's job is the join between the
database (context, rows) and the pure engine. The engine's own rules are tested without a
database in test_policy_engine.py; here the question is what reaches the table and what the
model is told.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.agent.gate import WriteGate
from agent_lab.agent.policy.rules import LoadedPolicy, NoParams, load_policies
from agent_lab.agent.policy.types import ChangeMeeting
from agent_lab.agent.tools_tasks import WriteTasksArgs
from agent_lab.models import Task

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 9, 21, 12, tzinfo=IST)  # Monday


def thu(hour: int, minute: int = 0) -> str:
    return dt.datetime(2026, 9, 24, hour, minute, tzinfo=IST).isoformat()


def _gate(session: Session, policies: list[LoadedPolicy] | None = None) -> WriteGate:
    rows = policies if policies is not None else load_policies(session)
    return WriteGate(session, rows, now=lambda: NOW, tz="Asia/Kolkata", run_id=None)


def _write(gate: WriteGate, **args: Any) -> dict[str, Any]:
    return gate.write_tasks(WriteTasksArgs.model_validate(args))


def _task(session: Session, text: str, *, agreed_by_me: bool = False) -> Task:
    task = Task(text=text, agreed_by_me=agreed_by_me, created_at=NOW)
    session.add(task)
    session.flush()
    return task


def _texts(session: Session) -> list[str]:
    return [t.text for t in session.execute(select(Task).order_by(Task.id)).scalars()]


def test_marking_an_unconfirmed_task_done_is_refused_by_name_and_not_written(
    session: Session,
) -> None:
    task = _task(session, "Send the recording")
    gate = _gate(session)

    out = _write(gate, update=[{"id": task.id, "status": "done"}])

    assert session.get(Task, task.id).status == "open"
    assert out["updated"] == []
    (refused,) = out["refused"]
    assert refused["item"] == "update[0]" and refused["rule"] == "never_mark_done"
    assert "Refused by never_mark_done (hard)" in refused["message"]
    (step,) = gate.drain()
    assert step.kind == "policy" and step.tool_output["outcome"] == "refuse"
    assert step.tool_input == {
        "item": "update[0]",
        "action": "task_change",
        "task_id": task.id,
        "new_status": "done",
    }


def test_a_task_i_confirmed_may_be_marked_done_and_any_task_may_be_dropped(
    session: Session,
) -> None:
    confirmed = _task(session, "Send deck", agreed_by_me=True)
    other = _task(session, "Send notes")

    out = _write(
        _gate(session),
        update=[{"id": confirmed.id, "status": "done"}, {"id": other.id, "status": "dropped"}],
    )

    assert out["updated"] == [confirmed.id, other.id]
    assert out["refused"] == [] and out["awaiting_approval"] == []
    assert session.get(Task, confirmed.id).status == "done"
    assert session.get(Task, other.id).status == "dropped"


def test_each_item_is_decided_alone_and_only_the_allowed_ones_are_written(
    session: Session,
) -> None:
    gate = _gate(session)

    out = _write(
        gate,
        create=[
            {"text": "Draft the pricing note", "due_at": thu(10, 30)},  # focus block: hard
            {"text": "Review the board pack", "due_at": thu(20)},  # after hours: middle
            {"text": "Prepare the client call with Acme", "due_at": thu(15)},  # soft note
            {"text": "Book the venue"},  # no due_at: nothing to place
        ],
    )

    assert _texts(session) == ["Prepare the client call with Acme", "Book the venue"]
    assert len(out["created"]) == 2
    assert [r["item"] for r in out["refused"]] == ["create[0]"]
    assert out["refused"][0]["rule"] == "focus_block"
    (held,) = out["awaiting_approval"]
    assert held["item"] == "create[1]" and held["rule"] == "working_hours"
    assert "Awaiting my approval under working_hours (middle)" in held["message"]
    assert "top-urgent" in held["message"], "it says what would have to be true to proceed"
    (note,) = out["notes"]
    assert note.startswith("create[2]: Preference set aside (client_calls_late_morning)")
    # one decision per action: a TaskChange for every item, plus a PlaceSlot per due_at
    steps = gate.drain()
    assert [(s.tool_input["item"], s.tool_input["action"]) for s in steps] == [
        ("create[0]", "task_change"),
        ("create[0]", "place_slot"),
        ("create[1]", "task_change"),
        ("create[1]", "place_slot"),
        ("create[2]", "task_change"),
        ("create[2]", "place_slot"),
        ("create[3]", "task_change"),
    ]
    assert [s.tool_output["outcome"] for s in steps] == [
        "allow",
        "refuse",
        "allow",
        "ask_override",
        "allow",
        "allow",
        "allow",
    ]
    assert gate.drain() == [], "drained steps are handed over once"


def test_a_due_at_is_the_minute_before_the_deadline(session: Session) -> None:
    """Due 19:00 sits inside working hours that end at 19:00; due 09:00 is not in the focus
    block that starts at 09:00. Both are allowed."""
    out = _write(
        _gate(session),
        create=[
            {"text": "Send the minutes", "due_at": thu(19)},
            {"text": "Ping", "due_at": thu(9)},
        ],
    )
    # 09:00 is outside working hours (10:00 onwards), so that one is held, not refused
    assert [r["rule"] for r in out["refused"]] == []
    assert [h["rule"] for h in out["awaiting_approval"]] == ["working_hours"]
    assert _texts(session) == ["Send the minutes"]


def test_an_update_that_moves_a_due_at_into_the_focus_block_is_refused(session: Session) -> None:
    task = _task(session, "Send the recording")
    out = _write(_gate(session), update=[{"id": task.id, "due_at": thu(10)}])
    assert [r["rule"] for r in out["refused"]] == ["focus_block"]
    assert session.get(Task, task.id).due_at is None


def test_a_naive_due_at_is_a_readable_error_and_is_not_written(session: Session) -> None:
    out = _write(_gate(session), create=[{"text": "Ping", "due_at": "2026-09-24T15:00:00"}])
    (error,) = out["policy_errors"]
    assert error["item"] == "create[0]" and "has no timezone" in error["message"]
    assert _texts(session) == []


def test_a_rule_the_engine_cannot_evaluate_is_a_readable_error_not_a_pass(
    session: Session,
) -> None:
    rows = load_policies(session)
    ghost = LoadedPolicy(
        id=10_000,
        code="no_friday_deploys",
        tier="hard",
        description="x",
        params=NoParams(),
        active=True,
    )

    out = _write(_gate(session, [*rows, ghost]), create=[{"text": "Book the venue"}])

    (error,) = out["policy_errors"]
    assert "UnknownRule" in error["error"] and "no_friday_deploys" in error["error"]
    assert _texts(session) == []


def test_a_context_that_lacks_the_meeting_is_a_readable_error(session: Session) -> None:
    gate = _gate(session)
    start = dt.datetime(2026, 9, 24, 15, tzinfo=IST)

    verdict = gate.decide(
        ChangeMeeting(meeting_id=999_999, new_start=start, new_end=start + dt.timedelta(hours=1)),
        item="probe",
    )

    assert isinstance(verdict, str) and verdict.startswith("IncompleteContext: meeting 999999")
    (step,) = gate.drain()
    assert step.tool_output == {"outcome": "error", "error": verdict}


def test_switching_a_rule_off_in_the_rows_lets_the_write_through(session: Session) -> None:
    rows = [
        dataclasses.replace(p, active=False) if p.code == "focus_block" else p
        for p in load_policies(session)
    ]
    out = _write(_gate(session, rows), create=[{"text": "Draft", "due_at": thu(10, 30)}])
    assert out["refused"] == [] and _texts(session) == ["Draft"]
