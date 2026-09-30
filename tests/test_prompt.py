"""The system prompt: a stable, cacheable prefix that carries the rules the model must know.

The rules and their numbers are rendered from the loaded ``policies`` rows, never typed into
the prompt, so a data edit to a row changes what the model is told with no release.
"""

import datetime as dt

from agent_lab.agent.prompt import system_prompt
from policy_seed import SEED, deactivate, seed_policies

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TUESDAY = dt.datetime(2026, 9, 8, 9, 30, tzinfo=IST)


def test_the_prompt_is_identical_for_two_tasks_on_the_same_day() -> None:
    policies = seed_policies()
    assert system_prompt(now=TUESDAY, policies=policies) == system_prompt(
        now=TUESDAY, policies=policies
    )


def test_the_prompt_names_the_date_the_timezone_the_tools_and_the_hard_rule() -> None:
    text = system_prompt(now=TUESDAY, policies=seed_policies())
    assert "2026-09-08" in text and "Tuesday" in text
    assert "Asia/Kolkata" in text
    for tool in ("read_calendar_window", "search_transcripts", "write_tasks"):
        assert tool in text
    assert "never mark a task done" in text.lower()
    assert "focus block" in text.lower()


def test_every_active_rule_is_in_the_prompt_by_code_with_its_numbers_from_the_row() -> None:
    text = system_prompt(now=TUESDAY, policies=seed_policies())
    for code, *_ in SEED:
        assert code in text
    assert "09:00 to 11:00 Asia/Kolkata, Mon, Tue, Wed, Thu, Fri" in text  # focus_block
    assert "10:00 to 19:00 Asia/Kolkata, Mon, Tue, Wed, Thu, Fri" in text  # working_hours
    assert "at most 6" in text  # max_meetings_per_day
    assert "important, fixed" in text  # fixed_meeting_immutable markers


def test_editing_a_row_changes_the_prompt_and_an_inactive_rule_leaves_it() -> None:
    moved = seed_policies(
        focus_block={"start": "08:00", "end": "10:30", "days": [1, 2, 3], "tz": "Asia/Kolkata"}
    )
    text = system_prompt(now=TUESDAY, policies=moved)
    assert "08:00 to 10:30 Asia/Kolkata, Mon, Tue, Wed" in text
    assert "09:00 to 11:00" not in text

    off = system_prompt(now=TUESDAY, policies=deactivate(seed_policies(), "prefer_short_slots"))
    assert "prefer_short_slots" not in off
