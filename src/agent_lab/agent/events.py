"""Step events as JSON lines: the loop's first observability seam.

One line per step, plain keys, no nesting deeper than a tool's own output. Anything that can
read stdout can follow a run as it happens; a UI can do the same later without touching the
loop. The model's text is included; the model's private reasoning is not available and is
not reported.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from agent_lab.agent.types import Step


class JsonLinesSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def __call__(self, step: Step) -> None:
        self._stream.write(json.dumps(as_event(step), default=str) + "\n")
        self._stream.flush()


def as_event(step: Step) -> dict[str, Any]:
    base: dict[str, Any] = {"ordinal": step.ordinal, "latency_ms": step.latency_ms}
    if step.kind == "model":
        usage = step.usage
        return {
            "event": "model_reply",
            **base,
            "text": step.text,
            "tokens": {"in": usage.input_tokens, "out": usage.output_tokens} if usage else None,
        }
    if step.kind == "policy":
        return {
            "event": "policy_decision",
            **base,
            "tool": step.tool_name,
            "action": step.tool_input,
            "decision": step.tool_output,
        }
    return {
        "event": "tool_finished",
        **base,
        "tool": step.tool_name,
        "input": step.tool_input,
        "output": step.tool_output,
    }
