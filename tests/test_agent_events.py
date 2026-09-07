"""Phase 3: every step is observable as it happens, not only after the run.

The loop takes an on_step callback; a JSON-lines sink is the first consumer. This is the seam
a cockpit UI would plug into later, so the event shape is deliberately plain.
"""

import io
import json

from agent_lab.agent.events import JsonLinesSink
from agent_lab.agent.loop import run
from agent_lab.agent.types import ModelReply, ToolCall, Usage
from fakes import ScriptedModel


def test_each_step_is_reported_as_it_happens_in_order() -> None:
    from pydantic import BaseModel

    from agent_lab.agent.tools import Tool

    class NoArgs(BaseModel):
        pass

    tool = Tool(name="ping", description="Does nothing.", args_model=NoArgs, fn=lambda a: "pong")
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="ping", arguments={}, id="c1"),),
                usage=Usage(10, 4),
            ),
            ModelReply(text="done", tool_calls=(), usage=Usage(12, 2)),
        ]
    )
    seen: list = []

    result = run(task="ping once", model=model, tools=[tool], max_steps=4, on_step=seen.append)

    assert [s.kind for s in seen] == ["model", "tool", "model"]
    assert seen == result.steps, "the callback saw exactly the steps the result holds"
    assert all(isinstance(s.latency_ms, int) and s.latency_ms >= 0 for s in seen)


def test_the_json_lines_sink_writes_one_parseable_line_per_step() -> None:
    from pydantic import BaseModel

    from agent_lab.agent.tools import Tool

    class NoArgs(BaseModel):
        pass

    tool = Tool(name="ping", description="Does nothing.", args_model=NoArgs, fn=lambda a: "pong")
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="ping", arguments={}, id="c1"),),
                usage=Usage(10, 4),
            ),
            ModelReply(text="done", tool_calls=(), usage=Usage(12, 2)),
        ]
    )
    stream = io.StringIO()

    run(task="ping once", model=model, tools=[tool], max_steps=4, on_step=JsonLinesSink(stream))

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [e["event"] for e in lines] == ["model_reply", "tool_finished", "model_reply"]
    assert lines[1]["tool"] == "ping" and lines[1]["output"] == "pong"
    assert lines[0]["tokens"] == {"in": 10, "out": 4}
    assert all("latency_ms" in e and "ordinal" in e for e in lines)
