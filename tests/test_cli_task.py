"""Phase 4 offline: `make task t="..."` runs a task end to end and prints the trace as events.

With no provider configured the CLI runs a scripted model from a JSON file and says so loudly,
so the plumbing (CLI -> execute -> tools -> Postgres -> events) is proven before any key exists.
"""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_lab.agent.cli import main
from agent_lab.models import AgentRun

SCRIPT = Path(__file__).parent / "fixtures" / "script_plan_week.json"


def test_the_cli_runs_a_scripted_task_prints_events_and_records_the_run(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    before = {r.id for r in session.execute(select(AgentRun)).scalars()}
    assert os.environ["DATABASE_URL"].endswith("_test"), "the CLI must hit the test database"

    code = main(["--script", str(SCRIPT), "Plan my week"])

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    events = [json.loads(line) for line in lines if line.startswith("{")]
    assert code == 0
    assert [e["event"] for e in events] == ["model_reply", "tool_finished", "model_reply"]
    assert events[1]["tool"] == "read_calendar_window"
    assert "SCRIPTED MODEL" in out, "no real provider is configured and the output says so"
    assert "Scripted answer" in out
    # the run reached the database, in a committed transaction of its own
    session.expire_all()
    after = {r.id for r in session.execute(select(AgentRun)).scalars()}
    new = after - before
    assert len(new) == 1
    run = session.get(AgentRun, new.pop())
    assert run.outcome == "completed" and run.task == "Plan my week"
