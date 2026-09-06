"""The loop: ask the model; run any tools it asked for; feed the results back; repeat."""

from __future__ import annotations

import json
from typing import Any

from agent_lab.agent.tools import Tool
from agent_lab.agent.types import ModelClient, RunResult, Step


def run(task: str, model: ModelClient, tools: list[Tool], max_steps: int) -> RunResult:
    by_name = {t.name: t for t in tools}
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    steps: list[Step] = []

    while True:
        reply = model.complete(messages, tools)
        steps.append(Step(ordinal=len(steps) + 1, kind="model", usage=reply.usage, text=reply.text))
        if not reply.tool_calls:
            return RunResult(outcome="completed", answer=reply.text, steps=steps)

        messages.append(
            {"role": "assistant", "content": reply.text, "tool_calls": list(reply.tool_calls)}
        )
        for call in reply.tool_calls:
            output = by_name[call.name].call(call.arguments)
            steps.append(
                Step(
                    ordinal=len(steps) + 1,
                    kind="tool",
                    tool_name=call.name,
                    tool_input=call.arguments,
                    tool_output=output,
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(output),
                }
            )
