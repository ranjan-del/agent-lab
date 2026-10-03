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


def _policy_step(ordinal: int, outcome: str, **decision: object) -> Step:
    return Step(
        ordinal=ordinal,
        kind="policy",
        tool_name="write_tasks",
        tool_input={"item": "create[0]", "action": "task_change"},
        tool_output={"outcome": outcome, **decision},
        latency_ms=1,
    )


def test_policy_steps_are_stored_and_the_first_refusal_fills_the_run(session: Session) -> None:
    from agent_lab.models import Policy

    focus, done = (
        session.execute(select(Policy.id).where(Policy.code == code)).scalar_one()
        for code in ("focus_block", "never_mark_done")
    )
    result = RunResult(
        outcome="completed",
        answer="One task was refused.",
        steps=[
            Step(ordinal=1, kind="model", usage=Usage(10, 2)),
            _policy_step(2, "allow", notes=[]),
            _policy_step(3, "ask_override", policy_id=focus, code="working_hours"),
            _policy_step(
                4,
                "refuse",
                policy_id=focus,
                code="focus_block",
                reason="Never place or propose anything inside the focus block.",
                detail="it overlaps the block",
            ),
            _policy_step(
                5, "refuse", policy_id=done, code="never_mark_done", reason="x", detail=""
            ),
            Step(ordinal=6, kind="tool", tool_name="write_tasks", tool_input={}, tool_output={}),
            Step(ordinal=7, kind="model", usage=Usage(10, 2), text="One task was refused."),
        ],
    )
    now = dt.datetime(2026, 9, 21, 12, tzinfo=IST)

    run = record_run(
        session, task="t", result=result, model_name="scripted", started_at=now, finished_at=now
    )

    steps = (
        session.execute(select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal))
        .scalars()
        .all()
    )
    assert [s.kind for s in steps] == [
        "model",
        "policy",
        "policy",
        "policy",
        "policy",
        "tool",
        "model",
    ]
    assert steps[3].input == {"item": "create[0]", "action": "task_change"}
    assert steps[3].output["outcome"] == "refuse"
    # the FIRST refusal names the run; an ask is not a refusal
    assert run.policy_id == focus
    assert run.refusal_reason == (
        "focus_block: Never place or propose anything inside the focus block. it overlaps the block"
    )


def test_a_run_with_no_refusal_leaves_the_refusal_columns_empty(session: Session) -> None:
    now = dt.datetime(2026, 9, 21, 12, tzinfo=IST)
    result = RunResult(outcome="completed", answer="ok", steps=[_policy_step(1, "allow")])
    run = record_run(
        session, task="t", result=result, model_name="scripted", started_at=now, finished_at=now
    )
    assert run.policy_id is None and run.refusal_reason is None
