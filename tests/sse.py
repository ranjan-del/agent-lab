"""Parse a text/event-stream body back into events, the way a browser's EventSource would."""

from __future__ import annotations

import json
from typing import Any


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
