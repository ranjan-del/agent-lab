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


# ---------------------------------------------------------------------------------------------
# Phase 3: the five middle rules ask rather than refuse or allow.
# ---------------------------------------------------------------------------------------------
HARD_AND_MIDDLE = seed_policies(("hard", "middle"))


def assert_asks(decision: Decision, code: str) -> None:
    assert decision.outcome is Outcome.ASK_OVERRIDE
    assert decision.code == code
    assert decision.tier == "middle"
    assert decision.reason == DESCRIPTIONS[code]
    assert decision.also == ()
    assert decision.needs is not None and code in decision.needs


def test_a_seventh_meeting_in_one_day_asks() -> None:
    """Six half-hour meetings on Tuesday, well spaced: three hours, so only the count fires."""
    day = [meeting(i, at(22, h), at(22, h, 30)) for i, h in enumerate((11, 12, 13, 15, 16, 17))]
    decision = evaluate(slot(at(22, 18), at(22, 18, 30)), context(meetings=day), HARD_AND_MIDDLE)
    assert_asks(decision, "max_meetings_per_day")
    assert "7 meetings" in decision.detail and "6" in decision.detail


def test_a_day_past_the_meeting_hours_limit_asks() -> None:
    """Three and a half hours booked; one more hour makes four and a half, over four."""
    day = [meeting(1, at(22, 11), at(22, 13)), meeting(2, at(22, 14), at(22, 15, 30))]
    decision = evaluate(slot(at(22, 16), at(22, 17)), context(meetings=day), HARD_AND_MIDDLE)
    assert_asks(decision, "max_meeting_hours_per_day")
    assert "4.5" in decision.detail


def test_back_to_back_meetings_without_the_gap_ask() -> None:
    day = [meeting(1, at(22, 14), at(22, 15))]
    decision = evaluate(slot(at(22, 15, 5), at(22, 15, 35)), context(meetings=day), HARD_AND_MIDDLE)
    assert_asks(decision, "min_gap_between_meetings")
    assert "5 minutes" in decision.detail and "15" in decision.detail


def test_anything_outside_working_hours_or_on_a_weekend_asks() -> None:
    saturday = evaluate(slot(at(26, 12), at(26, 12, 30)), context(), HARD_AND_MIDDLE)
    assert_asks(saturday, "working_hours")
    evening = evaluate(slot(at(22, 18, 45), at(22, 19, 15)), context(), HARD_AND_MIDDLE)
    assert_asks(evening, "working_hours")


def test_changing_a_meeting_inside_the_notice_period_asks() -> None:
    """Monday 17:00 to Tuesday 15:00 is 22 hours, inside the 24-hour notice."""
    review = meeting(4, at(22, 15), at(22, 16))
    move = ChangeMeeting(meeting_id=4, new_start=at(22, 16, 30), new_end=at(22, 17, 30))
    decision = evaluate(move, context(meetings=[review], now=at(21, 17)), HARD_AND_MIDDLE)
    assert_asks(decision, "no_change_within_notice")
    # The same move proposed two days out is not inside the notice and passes.
    early = evaluate(move, context(meetings=[review], now=at(20, 12)), HARD_AND_MIDDLE)
    assert early.outcome is Outcome.ALLOW


def test_switching_a_rule_off_in_the_database_changes_the_decision(session) -> None:
    """A data edit, no code change: the same Saturday slot asks, then is allowed."""
    from sqlalchemy import update

    from agent_lab.agent.policy.rules import load_policies
    from agent_lab.models import Policy

    saturday = slot(at(26, 12), at(26, 12, 30))
    before = evaluate(saturday, context(), load_policies(session))
    assert before.outcome is Outcome.ASK_OVERRIDE and before.code == "working_hours"

    session.execute(update(Policy).where(Policy.code == "working_hours").values(active=False))
    session.flush()
    after = evaluate(saturday, context(), load_policies(session))
    assert after.outcome is Outcome.ALLOW
    assert after.code is None


def test_a_hard_rule_beats_a_middle_rule_that_also_fires() -> None:
    """09:30 to 10:30 Tuesday is inside the focus block (hard) and before working hours
    (middle). The answer is a refusal, not a question, and the middle rule is still named."""
    decision = evaluate(slot(at(22, 9, 30), at(22, 10, 30)), context(), HARD_AND_MIDDLE)
    assert_refused(decision, "focus_block")
    assert [h.code for h in decision.also] == ["working_hours"]
    assert decision.needs is None


def test_two_middle_rules_ask_once_and_the_question_names_both() -> None:
    """A Saturday slot with no gap after a Saturday meeting breaks two middle rules."""
    day = [meeting(1, at(26, 12), at(26, 13))]
    decision = evaluate(slot(at(26, 13), at(26, 13, 30)), context(meetings=day), HARD_AND_MIDDLE)
    assert decision.outcome is Outcome.ASK_OVERRIDE
    codes = [decision.code, *(h.code for h in decision.also)]
    assert codes == ["min_gap_between_meetings", "working_hours"]
    assert decision.needs is not None
    assert all(c in decision.needs for c in codes)


# ---------------------------------------------------------------------------------------------
# Phase 4: the four soft rules never block. They advise on an ALLOW and say it was set aside.
# ---------------------------------------------------------------------------------------------
ALL = seed_policies()


def assert_advises(decision: Decision, code: str) -> str:
    assert decision.outcome is Outcome.ALLOW
    assert decision.code is None
    assert [n.code for n in decision.notes] == [code]
    note = decision.notes[0].note
    assert "set aside" in note and code in note and DESCRIPTIONS[code] in note
    return note


def test_a_slot_on_a_heavier_day_is_allowed_with_a_note_naming_the_lighter_day() -> None:
    tuesday = [meeting(1, at(22, 11), at(22, 11, 30)), meeting(2, at(22, 12), at(22, 12, 30))]
    ctx = context(meetings=tuesday, candidate_days=[dt.date(2026, 9, 22), dt.date(2026, 9, 23)])
    note = assert_advises(
        evaluate(slot(at(22, 14), at(22, 14, 30)), ctx, ALL), "prefer_lightest_day"
    )
    assert "2026-09-23" in note
    # On the lighter day itself, nothing is set aside.
    assert evaluate(slot(at(23, 14), at(23, 14, 30)), ctx, ALL).notes == ()


def test_a_one_to_one_away_from_the_others_is_allowed_with_a_note() -> None:
    thursday = [meeting(1, at(24, 15), at(24, 15, 30), one_to_one=True)]
    one_to_one = slot(at(22, 14), at(22, 14, 30), is_one_to_one=True)
    note = assert_advises(
        evaluate(one_to_one, context(meetings=thursday), ALL), "keep_one_to_ones_same_day"
    )
    assert "2026-09-24" in note


def test_a_long_slot_is_allowed_with_a_note_that_a_short_one_was_preferred() -> None:
    note = assert_advises(
        evaluate(slot(at(22, 14), at(22, 15)), context(), ALL), "prefer_short_slots"
    )
    assert "60 minutes" in note and "30" in note


def test_a_client_call_outside_late_morning_is_allowed_with_a_note() -> None:
    afternoon = slot(at(22, 15), at(22, 15, 30), is_client_call=True)
    note = assert_advises(evaluate(afternoon, context(), ALL), "client_calls_late_morning")
    assert "11:00 to 13:00" in note
    late_morning = slot(at(22, 11, 30), at(22, 12), is_client_call=True)
    assert evaluate(late_morning, context(), ALL).notes == ()


def test_a_middle_rule_beats_a_soft_rule_and_the_question_carries_no_notes() -> None:
    """A 60-minute Saturday slot: working hours (middle) and short slots (soft) both fire.
    The action is not allowed yet, so there is nothing to set aside: ask, with no notes."""
    decision = evaluate(slot(at(26, 12), at(26, 13)), context(), ALL)
    assert decision.outcome is Outcome.ASK_OVERRIDE
    assert decision.code == "working_hours"
    assert decision.notes == ()


def test_soft_rules_never_block_even_when_all_four_fire() -> None:
    thursday = [meeting(1, at(24, 15), at(24, 15, 30), one_to_one=True)]
    tuesday = [meeting(2, at(22, 11), at(22, 11, 30))]
    ctx = context(
        meetings=thursday + tuesday,
        candidate_days=[dt.date(2026, 9, 22), dt.date(2026, 9, 23)],
    )
    action = slot(at(22, 15), at(22, 16), is_one_to_one=True, is_client_call=True)
    decision = evaluate(action, ctx, ALL)
    assert decision.outcome is Outcome.ALLOW
    assert [n.code for n in decision.notes] == [
        "prefer_lightest_day",
        "keep_one_to_ones_same_day",
        "prefer_short_slots",
        "client_calls_late_morning",
    ]
