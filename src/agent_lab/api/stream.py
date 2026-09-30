"""``GET /runs/stream?task=...``: watch a run as it happens.

GET, not POST, because the browser's ``EventSource`` can only GET, and closing the tab that
holds one is the disconnect this module exists to handle. The task rides in the query string,
which is fine for a sentence and is why this is not the place to send a document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from agent_lab.agent.scripted import ScriptedModel
from agent_lab.config import settings
from agent_lab.sse import EventStreamResponse
from agent_lab.streaming import RunSource, ScriptedRunSource

router = APIRouter()


def get_run_source() -> RunSource:
    if settings.stream_script is None:
        return ScriptedRunSource(token_delay_s=settings.stream_token_delay_s)
    script = Path(settings.stream_script)
    return ScriptedRunSource(
        lambda: ScriptedModel.from_json(script), token_delay_s=settings.stream_token_delay_s
    )


def get_stream_timeout() -> float:
    return settings.stream_timeout_s


@router.get("/runs/stream", response_class=EventStreamResponse)
async def stream_run(
    task: Annotated[str, Query(min_length=1, max_length=500)],
    source: Annotated[RunSource, Depends(get_run_source)],
    timeout_s: Annotated[float, Depends(get_stream_timeout)],
) -> EventStreamResponse:
    """Stream a scripted run as server-sent events, ended by a deadline if it runs long."""
    return EventStreamResponse(source(task), timeout_s=timeout_s)
