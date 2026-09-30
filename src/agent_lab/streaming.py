"""A scripted run, told as it happens: one event per token, per tool call, and one at the end.

This is the producer behind ``GET /runs/stream``. It replays the same ``ScriptedModel`` the CLI
uses, so the stream works with no provider and no key, and its first event says so.

Tool calls are announced, not executed. Executing them needs a database session held open
for the life of a stream, and the provider adapter that would make a streamed run worth
executing is deferred by decision (W3 plan, carried debt item 4). The drill is the transport:
streaming, the deadline, and cancelling the work when the client leaves.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Protocol

import anyio

from agent_lab.agent.scripted import ScriptedModel
from agent_lab.agent.types import ModelClient, ModelReply, ToolCall, Usage
from agent_lab.sse import Event

DEMO_REPLIES: tuple[ModelReply, ...] = (
    ModelReply(
        text="Reading your calendar first.",
        tool_calls=(
            ToolCall(
                name="read_calendar_window",
                arguments={
                    "start": "2026-09-07T00:00:00+05:30",
                    "end": "2026-09-14T00:00:00+05:30",
                },
                id="c1",
            ),
        ),
        usage=Usage(1200, 30),
    ),
    ModelReply(
        text="Scripted answer: Tuesday is the heaviest day, so nothing new goes there.",
        tool_calls=(),
        usage=Usage(1800, 25),
    ),
)


class RunSource(Protocol):
    """Anything that turns a task into a stream of events. Tests substitute their own."""

    def __call__(self, task: str) -> AsyncGenerator[Event]: ...


def tokens(text: str) -> list[str]:
    """Word-sized tokens that keep their trailing space, so joining them restores the text."""
    return re.findall(r"\S+\s*", text)


class ScriptedRunSource:
    def __init__(
        self,
        model_factory: Callable[[], ModelClient] | None = None,
        *,
        token_delay_s: float = 0.05,
        max_steps: int = 8,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        # A factory, not a model: a ScriptedModel is consumed as it replays, so every request
        # needs a fresh one.
        self._model_factory = model_factory or (lambda: ScriptedModel(list(DEMO_REPLIES)))
        self._token_delay_s = token_delay_s
        self._max_steps = max_steps
        self._sleep = sleep

    async def __call__(self, task: str) -> AsyncGenerator[Event]:
        model = self._model_factory()
        yield Event("run_started", {"task": task, "scripted": True})
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        for ordinal in range(1, self._max_steps + 1):
            reply = model.complete(list(messages), [])
            for token in tokens(reply.text or ""):
                # The await is the point: it is where a cancellation lands. A real provider
                # client awaits the network here instead.
                await self._sleep(self._token_delay_s)
                yield Event("token", {"text": token, "ordinal": ordinal})
            if not reply.tool_calls:
                yield Event("done", {"outcome": "completed", "answer": reply.text})
                return
            messages.append({"role": "assistant", "content": reply.text})
            for call in reply.tool_calls:
                yield Event(
                    "tool_call",
                    {"tool": call.name, "input": call.arguments, "id": call.id, "executed": False},
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": "{}"})
        yield Event("done", {"outcome": "step_limit", "answer": None})
