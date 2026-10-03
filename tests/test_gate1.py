"""Gate 1: `make gate1` runs a real multi-step task end to end, through the policy gate.

The same two steps the make target takes, against the test database: ``seed_demo`` loads the
committed demo week and transcript, then the task CLI's ``main`` replays the gated script. The
run must read the calendar, search the transcripts, and propose three writes that come out as
one REFUSE (a hard rule), one ASK_OVERRIDE (a middle rule) and one ALLOW with a soft note, and
the CLI must print each decision and the summary. If any of that changes, Gate 1 has regressed.

The CLI commits. Its session joins the test's transaction through a savepoint, so its commit
releases the savepoint and the fixture's rollback still undoes everything.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agent_lab.agent.cli import main
from agent_lab.demo import seed_demo
from agent_lab.models import AgentRun, Meeting, RunStep, Task, Transcript

SCRIPT = Path(__file__).parent / "fixtures" / "script_plan_week_gated.json"
NOW = dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC)
TASK = "Turn what I promised on the Acme call into tasks for next week"


def test_the_demo_seed_is_idempotent(session: Session) -> None:
    first = seed_demo(session, now=NOW)
    second = seed_demo(session, now=NOW)

    assert (first.calendar.meetings_inserted, first.dropped, first.chunks_written) == (5, 0, 1)
    assert not first.calendar_skipped and not first.transcript_skipped
    assert second.calendar_skipped and second.transcript_skipped
    titles = session.execute(select(Meeting.title).where(Meeting.source == "ics")).scalars()
    assert "Board review (Important)" in set(titles)
    (transcript,) = session.execute(select(Transcript)).scalars().all()
    assert transcript.meeting_id is not None, "the transcript is linked to the Acme call"


def test_gate1_run_reaches_the_gate_and_gets_all_three_outcomes(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_demo(session, now=NOW)
    session.flush()
    factory = sessionmaker(
        bind=session.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False
    )

    code = main(["--script", str(SCRIPT), TASK], session_factory=factory)

    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert code == 0
    assert "SCRIPTED MODEL" in out

    # --- multi-step: read the calendar, search the transcripts, then propose the writes -----
    tools = [e["tool"] for e in events if e["event"] == "tool_finished"]
    assert tools == ["read_calendar_window", "search_transcripts", "write_tasks"]
    calendar, search, write = (e["output"] for e in events if e["event"] == "tool_finished")
    assert "Board review (Important)" in [m["title"] for m in calendar["meetings"]]
    (hit,) = search["results"]
    assert hit["meeting"]["title"] == "Acme client call"
    assert "revised pricing note" in hit["text"]

    # --- the gate: every decision is an event, and the three outcomes are the right ones ----
    decisions = [e for e in events if e["event"] == "policy_decision"]
    slots = [d for d in decisions if d["action"]["action"] == "place_slot"]
    assert [
        (d["action"]["title"], d["decision"]["outcome"], d["decision"]["code"]) for d in slots
    ] == [
        ("Send Acme the revised pricing note", "refuse", "focus_block"),
        ("Review the board pack", "ask_override", "working_hours"),
        ("Prepare the client call with Acme", "allow", None),
    ]
    assert slots[0]["decision"]["tier"] == "hard"
    assert slots[1]["decision"]["tier"] == "middle"
    (note,) = slots[2]["decision"]["notes"]
    assert note.startswith("Preference set aside (client_calls_late_morning)")
    assert all(
        d["decision"]["outcome"] == "allow"
        for d in decisions
        if d["action"]["action"] != "place_slot"
    )
    assert [r["rule"] for r in write["refused"]] == ["focus_block"]
    assert [h["rules"] for h in write["awaiting_approval"]] == [["working_hours"]]

    # --- what the CLI prints: a readable line per decision, and the summary -----------------
    assert out.count("  policy #") == 6
    assert "-> REFUSE focus_block (hard)" in out
    assert "-> ASK_OVERRIDE working_hours (middle)" in out
    assert "-> ALLOW with note: Preference set aside (client_calls_late_morning)" in out
    assert "steps: 13 (model 4, tool 3, policy 6)" in out
    assert "policy decisions: 6 (allow 4, refuse 1, ask_override 1, error 0)" in out
    assert "refused: focus_block on create[0]" in out
    assert "held for approval: working_hours on create[1]" in out

    # --- the database: the run names its refusal, and only the allowed task was written ----
    (run,) = session.execute(select(AgentRun).where(AgentRun.task == TASK)).scalars().all()
    assert run.outcome == "completed"
    assert run.refusal_reason.startswith("focus_block: ")
    kinds = session.execute(
        select(RunStep.kind).where(RunStep.run_id == run.id).order_by(RunStep.ordinal)
    ).scalars()
    assert list(kinds).count("policy") == 6
    (task,) = session.execute(select(Task).where(Task.created_by_run_id == run.id)).scalars().all()
    assert task.text == "Prepare the client call with Acme"
    assert f"tasks written: [{task.id}]" in out
