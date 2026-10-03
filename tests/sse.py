"""Parse a text/event-stream body back into events, the way a browser's EventSource would."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import anyio

from agent_lab.sse import Event


def parse_sse(body: str) -> list[dict[str, Any]]:
    """One dict per event: the JSON in ``data``, with ``_id`` and ``_event`` from the frame."""
    events: list[dict[str, Any]] = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        fields: dict[str, str] = {}
        for line in frame.splitlines():
            name, _, value = line.partition(": ")
            fields[name] = value
        data = json.loads(fields["data"])
        data["_id"] = fields.get("id")
        data["_event"] = fields.get("event")
        events.append(data)
    return events


class StallingSource:
    """Yields one token, then waits on an event nobody sets. Records how it was stopped.

    ``stopped`` is set on the way out, whatever the reason, so a test can wait for the proof
    instead of sleeping and hoping.
    """

    def __init__(self) -> None:
        self.started = anyio.Event()
        self.stopped = anyio.Event()
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
        finally:
            self.stopped.set()
