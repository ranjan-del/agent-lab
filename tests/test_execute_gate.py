"""The policy gate wired into execute(), end to end against a real Postgres.

A scripted multi-step run proposes three writes: one a hard rule refuses, one a middle rule
holds for my approval, one allowed with a soft preference set aside. Each decision must be a
run_steps row of kind "policy", the refusal must name the run, only the allowed task may
reach the table, and the model must have been told, in its own tool results, what happened.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.agent.execute import execute
from agent_lab.agent.types import ModelReply, ToolCall, Usage
from agent_lab.embeddings.fake import FakeEmbedder
from agent_lab.models import AgentRun, Meeting, Policy, RunStep, Task
from fakes import ScriptedModel

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 9, 21, 12, tzinfo=IST)  # Monday


def thu(hour: int, minute: int = 0) -> str:
    return dt.datetime(2026, 9, 24, hour, minute, tzinfo=IST).isoformat()


def _write(call_id: str, text: str, due_at: str) -> ModelReply:
    return ModelReply(
        text=None,
        tool_calls=(
            ToolCall(
                name="write_tasks",
                arguments={"create": [{"text": text, "urgency": "middle", "due_at": due_at}]},
                id=call_id,
            ),
        ),
        usage=Usage(100, 10),
    )


def _tool_result(model: ScriptedModel, call: int) -> dict[str, Any]:
    """What the model read back on its ``call``-th turn (1-based) from the last tool call."""
    last = model.seen_messages[call - 1][-1]
    assert last["role"] == "tool"
    result: dict[str, Any] = json.loads(last["content"])
    return result


def test_a_run_with_a_refusal_a_held_write_and_a_soft_note_is_traced_and_named(
    session: Session,
) -> None:
    session.add(
        Meeting(
            source="test",
            external_id="board",
            title="Board review (Important)",
            starts_at=dt.datetime(2026, 9, 24, 12, tzinfo=IST),
            ends_at=dt.datetime(2026, 9, 24, 13, tzinfo=IST),
            status="confirmed",
            raw={},
            ingested_at=NOW,
        )
    )
    session.flush()
    focus_id, hours_id, client_id = (
        session.execute(select(Policy.id).where(Policy.code == code)).scalar_one()
        for code in ("focus_block", "working_hours", "client_calls_late_morning")
    )
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(
                    ToolCall(
                        name="read_calendar_window",
                        arguments={
                            "start": "2026-09-21T00:00:00+05:30",
                            "end": "2026-09-28T00:00:00+05:30",
                        },
                        id="c1",
                    ),
                ),
                usage=Usage(100, 10),
            ),
            _write("c2", "Draft the pricing note", thu(10, 30)),  # focus block: hard
            _write("c3", "Review the board pack", thu(20)),  # after hours: middle
            _write("c4", "Prepare the client call with Acme", thu(15)),  # soft note
            ModelReply(
                text=(
                    "Saved one task. focus_block refused the pricing note; the board pack "
                    "awaits your approval under working_hours; I set aside "
                    "client_calls_late_morning for the Acme prep."
                ),
                tool_calls=(),
                usage=Usage(100, 10),
            ),
        ]
    )

    run, result = execute(
        task="Turn this week's promises into tasks",
        model=model,
        model_name="scripted",
        session=session,
        embedder=FakeEmbedder(),
        max_steps=6,
        now=lambda: NOW,
    )

    assert result.outcome == "completed"
    # --- the trace: every decision is a policy row, before the tool step it belongs to -----
    steps = (
        session.execute(select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal))
        .scalars()
        .all()
    )
    write = ["model", "policy", "policy", "tool"]
    assert [s.kind for s in steps] == ["model", "tool", *write, *write, *write, "model"]
    policy = [s for s in steps if s.kind == "policy"]
    assert [(s.input["action"], s.output["outcome"]) for s in policy] == [
        ("task_change", "allow"),
        ("place_slot", "refuse"),
        ("task_change", "allow"),
        ("place_slot", "ask_override"),
        ("task_change", "allow"),
        ("place_slot", "allow"),
    ]
    assert all(s.tool_name == "write_tasks" for s in policy)
    refused, held, noted = policy[1], policy[3], policy[5]
    assert (refused.output["code"], refused.output["policy_id"]) == ("focus_block", focus_id)
    assert (held.output["code"], held.output["policy_id"]) == ("working_hours", hours_id)
    assert "top-urgent" in held.output["needs"], "the question is recorded with the decision"
    (note,) = noted.output["notes"]
    assert note.startswith("Preference set aside (client_calls_late_morning)")
    assert client_id is not None

    # --- the run row: the refusal is named; the ask is not a refusal -----------------------
    stored = session.get(AgentRun, run.id)
    assert stored.policy_id == focus_id
    assert stored.refusal_reason.startswith(
        "focus_block: Never place or propose anything inside the focus block."
    )
    assert "Thu 2026-09-24 10:29 to 10:30 Asia/Kolkata" in stored.refusal_reason

    # --- the table: only the allowed task was written, stamped with the run ---------------
    (task,) = session.execute(select(Task)).scalars().all()
    assert task.text == "Prepare the client call with Acme"
    assert task.created_by_run_id == run.id

    # --- the model was told, in readable results, and the run carried on -----------------
    after_refusal = _tool_result(model, 3)
    assert after_refusal["created"] == []
    assert after_refusal["refused"][0]["rule"] == "focus_block"
    assert "Refused by focus_block (hard)" in after_refusal["refused"][0]["message"]
    after_ask = _tool_result(model, 4)
    assert after_ask["created"] == [] and after_ask["refused"] == []
    assert after_ask["awaiting_approval"][0]["rules"] == ["working_hours"]
    after_allow = _tool_result(model, 5)
    assert after_allow["created"] == [task.id]
    assert after_allow["notes"][0].startswith("create[0]: Preference set aside")
