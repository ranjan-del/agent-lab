"""The rule shapes: what a ``policies.rule`` JSONB is allowed to contain, per rule code.

No database. The database cannot validate JSONB shape, so these validators are the only
thing standing between a typo in a data edit and an engine that silently evaluates nothing.
"""

import datetime as dt

import pytest
from pydantic import ValidationError

from agent_lab.agent.policy.rules import (
    RULE_SHAPES,
    TIERS,
    DailyWindow,
    UnknownRule,
    parse_rule,
)


def test_every_spec_rule_has_exactly_one_shape() -> None:
    """Four hard, five middle, four soft: thirteen codes, and every one has a shape."""
    assert len(RULE_SHAPES) == 13
    assert TIERS == ("hard", "middle", "soft")


def test_focus_block_parses_into_real_times_and_a_real_zone() -> None:
    params = parse_rule(
        "focus_block",
        {"start": "09:00", "end": "11:00", "days": [1, 2, 3, 4, 5], "tz": "Asia/Kolkata"},
    )
    assert isinstance(params, DailyWindow)
    assert params.start == dt.time(9, 0)
    assert params.end == dt.time(11, 0)
    assert params.days == (1, 2, 3, 4, 5)
    assert params.zone.key == "Asia/Kolkata"


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(ValidationError, match="end"):
        parse_rule("focus_block", {"start": "11:00", "end": "09:00", "days": [1], "tz": "UTC"})


def test_a_made_up_timezone_is_refused() -> None:
    with pytest.raises(ValidationError, match="tz"):
        parse_rule(
            "focus_block", {"start": "09:00", "end": "11:00", "days": [1], "tz": "Mars/Olympus"}
        )


def test_a_weekday_outside_iso_1_to_7_is_refused() -> None:
    with pytest.raises(ValidationError, match="days"):
        parse_rule("working_hours", {"start": "10:00", "end": "19:00", "days": [0], "tz": "UTC"})


def test_a_misspelt_key_is_refused_not_ignored() -> None:
    """``maximum`` instead of ``max`` must fail loudly. Silently ignoring it leaves the rule
    with no number, which the engine would then evaluate as whatever the default is."""
    with pytest.raises(ValidationError, match="maximum"):
        parse_rule("max_meetings_per_day", {"maximum": 6})


def test_a_count_of_zero_or_less_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_rule("max_meetings_per_day", {"max": 0})


def test_a_rule_with_no_parameters_refuses_any_parameter() -> None:
    assert parse_rule("never_mark_done", {}) is not None
    with pytest.raises(ValidationError):
        parse_rule("never_mark_done", {"unless": "urgent"})


def test_an_unknown_code_is_an_error_not_a_pass() -> None:
    """A row nobody knows how to evaluate is a bug in the data, and must not load as harmless."""
    with pytest.raises(UnknownRule, match="no_such_rule"):
        parse_rule("no_such_rule", {})
