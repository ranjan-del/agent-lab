"""Server-sent events: one response that owns the whole life of the work it streams.

Why a Response subclass and not Starlette's ``StreamingResponse`` around a generator:

* **The deadline must not cross a ``yield``.** A cancel scope opened inside an async generator
  stays open while the generator is suspended, so a deadline that fires while the consumer is
  inside ``send()`` cancels the consumer, not the work. Here the scope lives in ``__call__``,
  which iterates the source and sends from one frame, so the deadline always lands on the
  work.
* **Disconnect cancels the work.** A watcher awaits ``http.disconnect`` beside the work and
  cancels it, so a client that leaves stops the producer, not just the response.
* **The source is always closed.** ``aclosing`` runs the source's ``finally`` on every exit:
  finished, timed out, or the connection gone.

A frame is ``id``, ``event`` and ``data`` lines and a blank line. ``data`` repeats the event
name inside the JSON so ``jq`` alone can read a stream without an SSE parser.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

import anyio
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


def encode(event: Event, event_id: int) -> bytes:
    payload = json.dumps({"event": event.name, **event.data}, default=str)
    return f"id: {event_id}\nevent: {event.name}\ndata: {payload}\n\n".encode()


class EventStreamResponse(Response):
    media_type = "text/event-stream"

    def __init__(self, events: AsyncGenerator[Event], *, timeout_s: float) -> None:
        self.events = events
        self.timeout_s = timeout_s
        self.status_code = 200
        self.background = None
        # no-cache: an intermediary must not hold a live stream to serve it later.
        # X-Accel-Buffering: nginx buffers proxied responses by default, which turns a stream
        # into one late lump. Neither header matters in tests; both matter behind a proxy.
        self.init_headers({"cache-control": "no-cache", "x-accel-buffering": "no"})
        self.sent = 0
        self.disconnected = False
        self.complete = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
        async with anyio.create_task_group() as work:
            # The watcher shares the work's cancel scope: a disconnect cancels the scope, and
            # the CancelledError lands wherever the source is awaiting, inside its own frame.
            work.start_soon(self._watch_for_disconnect, receive, work.cancel_scope)
            try:
                async with aclosing(self.events) as events:
                    with anyio.move_on_after(self.timeout_s) as deadline:
                        async for event in events:
                            await self._send_event(send, event)
                if deadline.cancelled_caught:
                    log.info("deadline %.3fs reached after %d events", self.timeout_s, self.sent)
                    await self._send_event(send, Event("timeout", {"after_s": self.timeout_s}))
                # Set before the final send: a server may deliver http.disconnect the moment the
                # response is complete, and the watcher can wake on it before being cancelled.
                self.complete = True
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            except OSError:
                # ASGI 2.4 servers raise from send() once the client is gone. aclosing has
                # already closed the source by the time this runs.
                self.disconnected = True
            work.cancel_scope.cancel()  # the stream is over; stop the watcher
        if self.disconnected:
            log.info("client left after %d events; the work was cancelled", self.sent)

    async def _watch_for_disconnect(self, receive: Receive, work: anyio.CancelScope) -> None:
        """Wait for the client to leave, then cancel the work.

        Listening, not polling ``request.is_disconnected()`` between events: a producer blocked
        on a slow model would never reach the next poll, so the check has to run beside it.
        Starlette does this itself only for servers below ASGI spec 2.4 (uvicorn is 2.3); doing
        it here makes the behaviour the same on every server.
        """
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                if self.complete:
                    return  # the normal end of a finished response, not a client leaving
                self.disconnected = True
                work.cancel()
                return

    async def _send_event(self, send: Send, event: Event) -> None:
        self.sent += 1
        body = encode(event, self.sent)
        await send({"type": "http.response.body", "body": body, "more_body": True})
