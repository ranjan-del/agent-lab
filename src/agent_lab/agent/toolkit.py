"""The three tools from SPEC section 2, bound to a session so the loop can call them.

The tool functions take a session and typed arguments. The loop only has the model's
arguments. This module closes that gap: each Tool here carries the session and the embedder,
and exposes only the argument schema to the model. The one writer, ``write_tasks``, is bound
to the run's ``WriteGate`` and cannot be built without one, so there is no ungated write path.

The descriptions quote rule numbers (working hours, the focus block) rendered from the loaded
policy rows, so what a tool tells the model and what the gate enforces cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from agent_lab.agent.gate import WriteGate
from agent_lab.agent.policy.rules import DailyWindow, LoadedPolicy, params_of
from agent_lab.agent.tools import Tool
from agent_lab.agent.tools_calendar import CalendarWindowArgs, read_calendar_window
from agent_lab.agent.tools_tasks import WriteTasksArgs
from agent_lab.agent.tools_transcripts import SearchTranscriptsArgs, search_transcripts
from agent_lab.embeddings.base import Embedder


def build_tools(
    session: Session,
    embedder: Embedder,
    *,
    policies: Sequence[LoadedPolicy],
    gate: WriteGate,
) -> list[Tool]:
    working_hours = params_of(policies, "working_hours", DailyWindow)
    focus = params_of(policies, "focus_block", DailyWindow)

    def calendar(args: CalendarWindowArgs) -> dict[str, Any]:
        return read_calendar_window(session, args, working_hours=working_hours)

    def transcripts(args: SearchTranscriptsArgs) -> dict[str, Any]:
        return search_transcripts(session, embedder, args)

    def tasks(args: WriteTasksArgs) -> dict[str, Any]:
        return gate.write_tasks(args)

    return [
        Tool(
            name="read_calendar_window",
            description=(
                "Read my calendar between two instants. Returns every meeting in the window, "
                "how many meetings and minutes each day holds, and the free slots inside "
                f"working hours ({working_hours.describe()}). Use it before proposing any time."
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
                "only I confirm completion. Use dropped for a task that no longer applies. "
                "Every task is checked against my rules before it is written, and a due_at is "
                f"checked as a time on my calendar: outside the focus block ({focus.describe()}) "
                f"and inside working hours ({working_hours.describe()}). The result lists, per "
                "task, what was written, refused, or held for my approval, and names the rule."
            ),
            args_model=WriteTasksArgs,
            fn=tasks,
        ),
    ]
