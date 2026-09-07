"""Phase 3: a finished run is written to agent_runs and run_steps, with its cost."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.agent.recorder import record_run
from agent_lab.agent.types import RunResult, Step, Usage
from agent_lab.models import AgentRun, RunStep

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def test_a_run_and_its_steps_are_written_with_tokens_cost_and_latency(session: Session) -> None:
    result = RunResult(
        outcome="completed",
        answer="Thursday is your lightest day.",
        steps=[
            Step(ordinal=1, kind="model", usage=Usage(1500, 20), text=None, latency_ms=800),
            Step(
                ordinal=2,
                kind="tool",
                tool_name="read_calendar_window",
                tool_input={
                    "start": "2026-09-07T00:00:00+05:30",
                    "end": "2026-09-14T00:00:00+05:30",
                },
                tool_output={"load_per_day": {"2026-09-10": {"meetings": 1, "minutes": 30}}},
                latency_ms=12,
            ),
            Step(
                ordinal=3,
                kind="model",
                usage=Usage(2500, 60),
                text="Thursday is your lightest day.",
                latency_ms=900,
            ),
        ],
    )
    started = dt.datetime(2026, 9, 8, 10, 0, tzinfo=IST)
    finished = started + dt.timedelta(milliseconds=1712)

    run = record_run(
        session,
        task="When am I free this week?",
        result=result,
        model_name="claude-sonnet-5",
        started_at=started,
        finished_at=finished,
    )

    stored = session.get(AgentRun, run.id)
    assert stored.task == "When am I free this week?"
    assert stored.outcome == "completed"
    assert stored.latency_ms == 1712
    # 4,000 input tokens at $2/MTok + 80 output tokens at $10/MTok
    assert float(stored.cost_usd) == 0.0088
    steps = (
        session.execute(select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal))
        .scalars()
        .all()
    )
    assert [s.kind for s in steps] == ["model", "tool", "model"]
    assert steps[1].tool_name == "read_calendar_window"
    assert steps[1].input["start"].startswith("2026-09-07")
    assert steps[1].output["load_per_day"]["2026-09-10"]["meetings"] == 1
    assert (steps[0].tokens_in, steps[0].tokens_out) == (1500, 20)
    assert steps[2].latency_ms == 900
