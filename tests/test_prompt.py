"""The system prompt: a stable, cacheable prefix that carries the rules the model must know."""

import datetime as dt

from agent_lab.agent.prompt import system_prompt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def test_the_prompt_is_identical_for_two_tasks_on_the_same_day() -> None:
    today = dt.datetime(2026, 9, 8, 9, 30, tzinfo=IST)
    assert system_prompt(now=today) == system_prompt(now=today)


def test_the_prompt_names_the_date_the_timezone_the_tools_and_the_hard_rule() -> None:
    text = system_prompt(now=dt.datetime(2026, 9, 8, 9, 30, tzinfo=IST))
    assert "2026-09-08" in text and "Tuesday" in text
    assert "Asia/Kolkata" in text
    for tool in ("read_calendar_window", "search_transcripts", "write_tasks"):
        assert tool in text
    assert "never mark a task done" in text.lower()
    assert "focus block" in text.lower()
