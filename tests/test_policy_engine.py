"""The policy engine: ``evaluate(action, context, policies) -> Decision``, pure and deterministic.

Every number the engine compares against comes from the migration 0006 seed rows, parsed
through their shapes exactly as ``load_policies`` would parse them. No database here except
where a test says so: the engine must decide without one.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_lab.agent.policy.engine import evaluate
from agent_lab.agent.policy.rules import TIERS, LoadedPolicy, Tier, parse_rule
from agent_lab.agent.policy.types import (
    AddAttendee,
    ChangeMeeting,
    Context,
    Decision,
    ExportContent,
    MeetingInfo,
    Outcome,
    PlaceSlot,
    ProposedAction,
    TaskChange,
)

IST = ZoneInfo("Asia/Kolkata")
ME = "me@example.org"
COLLEAGUE = "colleague@example.org"
OUTSIDER = "someone@client.example"


def _load_seed() -> list[tuple[str, str, str, str, dict[str, object]]]:
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0006_seed_policies.py"
    spec = importlib.util.spec_from_file_location("seed_0006", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seed: list[tuple[str, str, str, str, dict[str, object]]] = module.SEED
    return seed


SEED = _load_seed()
DESCRIPTIONS = {code: description for code, _kind, _sev, description, _rule in SEED}


def seed_policies(tiers: Iterable[Tier] = TIERS) -> tuple[LoadedPolicy, ...]:
    """The seed rows as the engine receives them, ids in seed order, all active."""
    wanted = set(tiers)
    return tuple(
        LoadedPolicy(
            id=i,
            code=code,
            tier=kind,  # type: ignore[arg-type]
            description=description,
            params=parse_rule(code, rule),
            active=True,
        )
        for i, (code, kind, _severity, description, rule) in enumerate(SEED, start=1)
        if kind in wanted
    )


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    """An instant in September 2026, IST. 21 Sep is a Monday, 26 Sep a Saturday."""
    return dt.datetime(2026, 9, day, hour, minute, tzinfo=IST)


def meeting(
    mid: int,
    start: dt.datetime,
    end: dt.datetime,
    *,
    labels: Iterable[str] = (),
    internal: bool = True,
    one_to_one: bool = False,
    client_call: bool = False,
) -> MeetingInfo:
    return MeetingInfo(
        id=mid,
        start=start,
        end=end,
        labels=frozenset(labels),
        is_internal=internal,
        is_one_to_one=one_to_one,
        is_client_call=client_call,
    )


def context(
    *,
    meetings: Iterable[MeetingInfo] = (),
    now: dt.datetime | None = None,
    confirmed_done: Iterable[int] = (),
    candidate_days: Iterable[dt.date] = (),
) -> Context:
    return Context(
        now=now or at(14, 12),
        tz="Asia/Kolkata",
        meetings=tuple(meetings),
        internal_people=frozenset({ME, COLLEAGUE}),
        confirmed_done_task_ids=frozenset(confirmed_done),
        candidate_days=tuple(candidate_days),
    )


def slot(start: dt.datetime, end: dt.datetime, **kwargs: object) -> PlaceSlot:
    return PlaceSlot(start=start, end=end, attendees=(ME, COLLEAGUE), **kwargs)  # type: ignore[arg-type]


def assert_refused(decision: Decision, code: str) -> None:
    assert decision.outcome is Outcome.REFUSE
    assert decision.code == code
    assert decision.tier == "hard"
    assert decision.reason == DESCRIPTIONS[code]
    assert decision.policy_id is not None


HARD = seed_policies(("hard",))


# ---------------------------------------------------------------------------------------------
# Phase 2: the four hard rules refuse, and the refusal names the rule.
# ---------------------------------------------------------------------------------------------
def test_an_action_no_hard_rule_touches_is_allowed() -> None:
    decision = evaluate(slot(at(22, 14), at(22, 14, 30)), context(), HARD)
    assert decision.outcome is Outcome.ALLOW
    assert decision.code is None
    assert decision.notes == ()


def test_moving_a_meeting_marked_fixed_is_refused_and_names_the_rule() -> None:
    board = meeting(1, at(22, 15), at(22, 16), labels={"Fixed"})
    move = ChangeMeeting(meeting_id=1, new_start=at(23, 15), new_end=at(23, 16))
    assert_refused(evaluate(move, context(meetings=[board]), HARD), "fixed_meeting_immutable")

    shorten = ChangeMeeting(meeting_id=1, new_start=at(22, 15), new_end=at(22, 15, 30))
    assert_refused(evaluate(shorten, context(meetings=[board]), HARD), "fixed_meeting_immutable")


def test_placing_anything_inside_the_focus_block_is_refused_and_names_the_rule() -> None:
    """Tuesday 10:30 to 11:30 IST overlaps the 09:00 to 11:00 block by half an hour."""
    decision = evaluate(slot(at(22, 10, 30), at(22, 11, 30)), context(), HARD)
    assert_refused(decision, "focus_block")
    assert "09:00" in decision.detail and "11:00" in decision.detail


def test_exposing_anything_outside_is_refused_and_names_the_rule() -> None:
    standup = meeting(3, at(22, 12), at(22, 12, 30), internal=True)
    add = AddAttendee(meeting_id=3, email=OUTSIDER)
    assert_refused(evaluate(add, context(meetings=[standup]), HARD), "no_external_exposure")

    leak = ExportContent(destination="email:" + OUTSIDER, includes_transcript=True)
    assert_refused(evaluate(leak, context(), HARD), "no_external_exposure")


def test_marking_a_task_done_i_did_not_confirm_is_refused_and_names_the_rule() -> None:
    done = TaskChange(task_id=7, new_status="done")
    assert_refused(evaluate(done, context(), HARD), "never_mark_done")
    # The same change, once I have confirmed it, is mine to make and the rule does not fire.
    assert evaluate(done, context(confirmed_done=[7]), HARD).outcome is Outcome.ALLOW


def test_evaluate_is_deterministic() -> None:
    """Same action, same context, same rows: the same decision, including when the rows
    arrive in a different order. This is the property W4's eval harness replays against."""
    board = meeting(1, at(22, 15), at(22, 16), labels={"important"})
    actions: list[ProposedAction] = [
        slot(at(22, 9, 30), at(22, 10)),
        ChangeMeeting(meeting_id=1, new_start=at(22, 9), new_end=at(22, 10)),
        TaskChange(task_id=1, new_status="done"),
        slot(at(22, 14), at(22, 14, 30)),
    ]
    for action in actions:
        first = evaluate(action, context(meetings=[board]), HARD)
        for _ in range(5):
            assert evaluate(action, context(meetings=[board]), HARD) == first
        assert evaluate(action, context(meetings=[board]), tuple(reversed(HARD))) == first


def test_the_engine_import_graph_has_no_database_session_and_no_model_client() -> None:
    """Import the engine in a clean interpreter and list what came with it."""
    probe = (
        "import sys, agent_lab.agent.policy.engine, agent_lab.agent.policy.types\n"
        "banned = ('sqlalchemy', 'psycopg', 'agent_lab.models', 'agent_lab.db',\n"
        "          'agent_lab.agent.loop', 'agent_lab.agent.execute', 'anthropic', 'openai',\n"
        "          'httpx', 'litellm')\n"
        "print(sorted(m for m in sys.modules if m.startswith(banned)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"
