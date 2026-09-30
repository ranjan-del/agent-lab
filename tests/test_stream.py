"""Module 5: a scripted run streamed as server-sent events, ended by a server-side deadline.

These tests use httpx's ASGITransport, which buffers the whole body before returning it. That
is fine here: both behaviours under test end the response from the server side. The tests
that need a client to go away mid-stream live in test_stream_disconnect.py.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator

import anyio
import httpx
import pytest

from agent_lab.agent.scripted import ScriptedModel
from agent_lab.agent.types import ModelReply, ToolCall, Usage
from agent_lab.api.stream import get_run_source, get_stream_timeout
from agent_lab.main import app
from agent_lab.sse import Event
from agent_lab.streaming import ScriptedRunSource
from sse import parse_sse


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _replies() -> list[ModelReply]:
    return [
        ModelReply(
            text=None,
            tool_calls=(ToolCall(name="read_calendar_window", arguments={"start": "x"}, id="c1"),),
            usage=Usage(10, 3),
        ),
        ModelReply(text="Your week is full on Tuesday.", tool_calls=(), usage=Usage(20, 7)),
    ]


async def test_a_scripted_run_streams_token_by_token_as_server_sent_events(
    http: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_run_source] = lambda: ScriptedRunSource(
        lambda: ScriptedModel(_replies()), token_delay_s=0
    )

    response = await http.get("/runs/stream", params={"task": "Plan my week"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = parse_sse(response.text)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_started" and events[0]["scripted"] is True
    assert kinds[1] == "tool_call" and events[1]["tool"] == "read_calendar_window"
    assert kinds[-1] == "done" and events[-1]["outcome"] == "completed"
    tokens = [e["text"] for e in events if e["event"] == "token"]
    assert len(tokens) == 6, "one event per word, not one per reply"
    assert "".join(tokens) == "Your week is full on Tuesday."
    assert events[-1]["answer"] == "Your week is full on Tuesday."
    # the SSE frame carries the same name as the JSON, and ids count up from 1
    assert all(e["_event"] == e["event"] for e in events)
    assert [e["_id"] for e in events] == [str(i) for i in range(1, len(events) + 1)]


async def test_the_default_source_needs_no_provider_and_no_database(
    http: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_run_source] = lambda: ScriptedRunSource(token_delay_s=0)

    response = await http.get("/runs/stream", params={"task": "anything"})

    events = parse_sse(response.text)
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "done"


class StallingSource:
    """Yields one token, then waits on an event nobody sets. Records how it was stopped."""

    def __init__(self) -> None:
        self.started = anyio.Event()
        self.stopped_by: type[BaseException] | None = None
        self.finished = False

    async def __call__(self, task: str) -> AsyncGenerator[Event]:
        try:
            yield Event("token", {"text": "thinking"})
            self.started.set()
            await anyio.Event().wait()
            self.finished = True
            yield Event("done", {"outcome": "completed"})
        except BaseException as exc:
            self.stopped_by = type(exc)
            raise


async def test_the_deadline_ends_the_stream_with_a_timeout_event_and_stops_the_work(
    http: httpx.AsyncClient,
) -> None:
    source = StallingSource()
    app.dependency_overrides[get_run_source] = lambda: source
    app.dependency_overrides[get_stream_timeout] = lambda: 0.02

    with anyio.fail_after(2):
        response = await http.get("/runs/stream", params={"task": "Plan my week"})

    events = parse_sse(response.text)
    assert [e["event"] for e in events] == ["token", "timeout"]
    assert events[-1]["after_s"] == 0.02
    assert source.stopped_by is anyio.get_cancelled_exc_class(), "the producer was cancelled"
    assert not source.finished, "and it never reached the end of its work"


async def test_a_missing_task_is_a_422_not_an_empty_stream(http: httpx.AsyncClient) -> None:
    response = await http.get("/runs/stream")
    assert response.status_code == 422
