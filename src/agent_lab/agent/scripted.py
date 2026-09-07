"""A model that replays pre-written replies. For offline runs and for tests.

Not a mock of a provider: it satisfies ModelClient exactly like a real client would, and the
loop cannot tell the difference. What it lacks is judgement, and every run that uses it says
so on its first line of output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_lab.agent.types import ModelReply, ToolCall, Usage


class ScriptedModel:
    def __init__(self, replies: list[ModelReply]) -> None:
        self._replies = list(replies)
        self.calls = 0
        self.seen_messages: list[list[dict[str, Any]]] = []

    @classmethod
    def from_json(cls, path: Path) -> ScriptedModel:
        """Each entry: text, tool_calls as [{name, arguments, id}], and usage as [in, out]."""
        raw = json.loads(path.read_text())
        replies = [
            ModelReply(
                text=entry.get("text"),
                tool_calls=tuple(
                    ToolCall(name=c["name"], arguments=c.get("arguments", {}), id=c.get("id", ""))
                    for c in entry.get("tool_calls", [])
                ),
                usage=Usage(*entry.get("usage", [0, 0])),
            )
            for entry in raw
        ]
        return cls(replies)

    def complete(self, messages: list[dict[str, Any]], tools: list[Any]) -> ModelReply:
        self.calls += 1
        self.seen_messages.append(list(messages))
        if not self._replies:
            raise RuntimeError("scripted model ran out of replies")
        return self._replies.pop(0)
