"""Module 5: closing the tab cancels the work, not just the response.

Neither Starlette's TestClient nor httpx's ASGITransport can show this: both run the app to
completion and hand back a buffered body, so the client never leaves mid-stream. Two
transports that can:

* **Raw ASGI.** The test is the server: it hands the app ``receive`` and ``send`` and delivers
  ``http.disconnect`` the moment the first event arrives. Deterministic, no sockets, and it
  runs at ASGI spec 2.3 and 2.4. At 2.4 Starlette stops listening for disconnects itself and
  only notices when a ``send`` fails, which never happens while the work is blocked. That is
  the case that proves the response does its own listening.
* **A real uvicorn on a loopback socket**, read by httpx as a stream and then closed, which
  is what a browser does when the tab goes. This is the end-to-end claim.

No test sleeps to wait for the outcome. Each waits on the producer's own ``stopped`` event
under a hard deadline, so a regression fails in two seconds instead of hanging.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import anyio
import httpx
import pytest
import uvicorn

from agent_lab.api.stream import get_run_source
from agent_lab.main import app
from sse import StallingSource, parse_sse


@pytest.fixture
def stalling() -> Iterator[StallingSource]:
    source = StallingSource()
    app.dependency_overrides[get_run_source] = lambda: source
    yield source
    app.dependency_overrides.clear()


def _scope(spec_version: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/runs/stream",
        "raw_path": b"/runs/stream",
        "query_string": b"task=Plan+my+week",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"accept", b"text/event-stream")],
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
    }


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_disconnect_mid_stream_cancels_the_producer(
    stalling: StallingSource, spec_version: str
) -> None:
    gone = anyio.Event()
    sent: list[dict[str, Any]] = []
    request_delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            gone.set()  # the tab closes as soon as the first event is on screen

    with anyio.fail_after(2):
        await app(_scope(spec_version), receive, send)
        await stalling.stopped.wait()

    assert stalling.stopped_by is anyio.get_cancelled_exc_class()
    assert not stalling.finished, "the work stopped before it completed"
    bodies = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert [e["event"] for e in parse_sse(bodies.decode())] == ["token"]
    assert not any(m.get("more_body") is False for m in sent), "nothing is sent to a closed tab"


async def test_closing_a_real_connection_cancels_the_producer(stalling: StallingSource) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve, [sock])
        with anyio.fail_after(5):
            while not server.started:
                await anyio.sleep(0.005)

            async with (
                httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client,
                client.stream("GET", "/runs/stream", params={"task": "Plan my week"}) as response,
            ):
                assert response.headers["content-type"].startswith("text/event-stream")
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        break  # first event received; now leave, as a closed tab does
            # leaving the block closed a half-read response, so httpx dropped the connection

            await stalling.stopped.wait()
        server.should_exit = True

    assert stalling.started.is_set()
    assert stalling.stopped_by is anyio.get_cancelled_exc_class()
    assert not stalling.finished
