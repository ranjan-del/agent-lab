"""The tool descriptions the model reads carry the rule numbers from the rows, not constants."""

import datetime as dt
from typing import cast

from sqlalchemy.orm import Session

from agent_lab.agent.gate import WriteGate
from agent_lab.agent.toolkit import build_tools
from agent_lab.embeddings.fake import FakeEmbedder
from policy_seed import seed_policies

NOW = dt.datetime(2026, 9, 21, 12, tzinfo=dt.UTC)


def _descriptions(**overrides: dict[str, object]) -> dict[str, str]:
    session = cast(Session, object())  # building the tools runs no query
    policies = seed_policies(**overrides)
    gate = WriteGate(session, policies, now=lambda: NOW, tz="Asia/Kolkata", run_id=None)
    tools = build_tools(session, FakeEmbedder(), policies=policies, gate=gate)
    return {t.name: t.description for t in tools}


def test_the_calendar_and_task_descriptions_quote_the_seeded_windows() -> None:
    d = _descriptions()
    assert "10:00 to 19:00 Asia/Kolkata" in d["read_calendar_window"]
    assert "10:00 to 19:00 Asia/Kolkata" in d["write_tasks"]
    assert "09:00 to 11:00 Asia/Kolkata" in d["write_tasks"]


def test_a_row_edit_changes_the_descriptions_with_no_code_change() -> None:
    d = _descriptions(
        working_hours={"start": "08:30", "end": "17:30", "days": [1, 2, 3, 4], "tz": "UTC"},
        focus_block={"start": "13:00", "end": "14:00", "days": [5], "tz": "UTC"},
    )
    assert "08:30 to 17:30 UTC, Mon, Tue, Wed, Thu" in d["read_calendar_window"]
    assert "13:00 to 14:00 UTC, Fri" in d["write_tasks"]
    assert "10:00" not in d["read_calendar_window"] + d["write_tasks"]
