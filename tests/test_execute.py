"""Phase 3, end to end offline: the real loop, the real tools, a real Postgres, a scripted model.

execute() is the one entry point: it opens the run row first so the tools can stamp what they
write with the run id, runs the loop, then closes the row with outcome, cost and latency.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.agent.execute import execute
from agent_lab.agent.types import ModelReply, ToolCall, Usage
from agent_lab.embeddings.fake import FakeEmbedder
from agent_lab.models import AgentRun, Meeting, RunStep, Task
from fakes import ScriptedModel

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def test_a_scripted_task_reads_the_calendar_writes_a_task_and_is_fully_recorded(
    session: Session,
) -> None:
    thu = dt.datetime(2026, 9, 10, 11, 0, tzinfo=IST)
    session.add(
        Meeting(
            source="test",
            external_id="demo",
            title="Client demo",
            starts_at=thu,
            ends_at=thu + dt.timedelta(hours=1),
            status="confirmed",
            raw={},
            ingested_at=dt.datetime.now(dt.UTC),
        )
    )
    session.flush()
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(
                    ToolCall(
                        name="read_calendar_window",
                        arguments={
                            "start": "2026-09-07T00:00:00+05:30",
                            "end": "2026-09-14T00:00:00+05:30",
                        },
                        id="c1",
                    ),
                ),
                usage=Usage(1200, 30),
            ),
            ModelReply(
                text=None,
                tool_calls=(
                    ToolCall(
                        name="write_tasks",
                        arguments={
                            "create": [{"text": "Send the demo recording", "urgency": "middle"}]
                        },
                        id="c2",
                    ),
                ),
                usage=Usage(1900, 40),
            ),
            ModelReply(
                text="Thursday has one meeting; I added one task.",
                tool_calls=(),
                usage=Usage(2100, 20),
            ),
        ]
    )

    run, result = execute(
        task="Plan my week and list what I owe",
        model=model,
        model_name="claude-sonnet-5",
        session=session,
        embedder=FakeEmbedder(),
        max_steps=6,
    )

    assert result.outcome == "completed"
    assert run.outcome == "completed" and run.finished_at is not None
    assert float(run.cost_usd) == round((5200 * 2 + 90 * 10) / 1_000_000, 6)
    # the calendar tool really read the database
    calendar_step = result.steps[1]
    assert calendar_step.tool_output["load_per_day"] == {
        "2026-09-10": {"meetings": 1, "minutes": 60}
    }
    # the task tool really wrote, and stamped the run that wrote it
    task = session.execute(select(Task)).scalar_one()
    assert task.text == "Send the demo recording"
    assert task.created_by_run_id == run.id
    # and the trace is in the database, step for step
    steps = (
        session.execute(select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal))
        .scalars()
        .all()
    )
    assert [s.kind for s in steps] == ["model", "tool", "model", "tool", "model"]
    assert [s.tool_name for s in steps if s.kind == "tool"] == [
        "read_calendar_window",
        "write_tasks",
    ]
    assert session.get(AgentRun, run.id) is run
