"""The three tools from SPEC section 2, bound to a session so the loop can call them.

The tool functions take a session and typed arguments. The loop only has the model's
arguments. This module closes that gap: each Tool here carries the session, the embedder, the
clock and the run id, and exposes only the argument schema to the model.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from agent_lab.agent.tools import Tool
from agent_lab.agent.tools_calendar import CalendarWindowArgs, read_calendar_window
from agent_lab.agent.tools_tasks import WriteTasksArgs, write_tasks
from agent_lab.agent.tools_transcripts import SearchTranscriptsArgs, search_transcripts
from agent_lab.embeddings.base import Embedder


def build_tools(
    session: Session,
    embedder: Embedder,
    *,
    now: Callable[[], dt.datetime],
    run_id: int | None,
) -> list[Tool]:
    def calendar(args: CalendarWindowArgs) -> dict[str, Any]:
        return read_calendar_window(session, args)

    def transcripts(args: SearchTranscriptsArgs) -> dict[str, Any]:
        return search_transcripts(session, embedder, args)

    def tasks(args: WriteTasksArgs) -> dict[str, Any]:
        return write_tasks(session, args, now=now(), run_id=run_id)

    return [
        Tool(
            name="read_calendar_window",
            description=(
                "Read my calendar between two instants. Returns every meeting in the window, "
                "how many meetings and minutes each day holds, and the free slots inside "
                "working hours (10:00 to 19:00 IST). Use it before proposing any time."
            ),
            args_model=CalendarWindowArgs,
            fn=calendar,
        ),
        Tool(
            name="search_transcripts",
            description=(
                "Search my meeting transcripts for a topic, a promise or a name. Returns the "
                "closest passages with the meeting each came from. Narrow with start and end "
                "when the question is about a particular day or week."
            ),
            args_model=SearchTranscriptsArgs,
            fn=transcripts,
        ),
        Tool(
            name="write_tasks",
            description=(
                "Create or update entries in my task list. Each task needs the text of what I "
                "agreed to do and an urgency: hard, middle or soft. Never set status to done; "
                "only I confirm completion. Use dropped for a task that no longer applies."
            ),
            args_model=WriteTasksArgs,
            fn=tasks,
        ),
    ]
