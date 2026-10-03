"""Tool argument models forbid unknown keys, so a misspelt key is a repairable error.

Pydantic's default is ``extra="ignore"``: ``{"creates": [...]}`` for ``write_tasks`` used to
validate as "create nothing", the run completed, and no row was written. That is silent
partial success. With ``extra="forbid"`` the same call comes back as an error that names the
key, and the model can fix it on its next turn (docs/notes/2026-09-30-tool-calling-...md).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from agent_lab.agent.loop import _invoke
from agent_lab.agent.tools import Tool
from agent_lab.agent.tools_calendar import CalendarWindowArgs
from agent_lab.agent.tools_tasks import NewTask, TaskUpdate, WriteTasksArgs
from agent_lab.agent.tools_transcripts import SearchTranscriptsArgs
from agent_lab.agent.types import ToolCall

VALID: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (NewTask, {"text": "Send the recording"}),
    (TaskUpdate, {"id": 1}),
    (WriteTasksArgs, {}),
    (
        CalendarWindowArgs,
        {"start": "2026-09-21T00:00:00+05:30", "end": "2026-09-22T00:00:00+05:30"},
    ),
    (SearchTranscriptsArgs, {"query": "pricing"}),
]


@pytest.mark.parametrize(("model", "valid"), VALID, ids=[m.__name__ for m, _ in VALID])
def test_every_tool_argument_model_refuses_an_unknown_key(
    model: type[BaseModel], valid: dict[str, Any]
) -> None:
    model.model_validate(valid)  # the valid call still validates
    with pytest.raises(ValidationError) as caught:
        model.model_validate({**valid, "bogus": 1})
    assert [e["loc"] for e in caught.value.errors()] == [("bogus",)]


def test_a_misspelt_creates_key_comes_back_as_a_readable_error_and_writes_nothing() -> None:
    written: list[WriteTasksArgs] = []

    def fn(args: WriteTasksArgs) -> dict[str, Any]:
        written.append(args)
        return {"created": [], "updated": []}

    tool = Tool(name="write_tasks", description="", args_model=WriteTasksArgs, fn=fn)
    call = ToolCall(
        name="write_tasks", arguments={"creates": [{"text": "Send the recording"}]}, id="c1"
    )

    out = _invoke({"write_tasks": tool}, call)

    assert written == [], "the tool must not run on a call it could not understand"
    assert out == {
        "error": "invalid arguments for 'write_tasks': creates: Extra inputs are not permitted"
    }


def test_a_misspelt_key_inside_a_nested_task_is_named_with_its_path() -> None:
    with pytest.raises(ValidationError) as caught:
        WriteTasksArgs.model_validate({"create": [{"text": "Send it", "urgncy": "hard"}]})
    assert [e["loc"] for e in caught.value.errors()] == [("create", 0, "urgncy")]
