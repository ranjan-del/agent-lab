"""The agent loop, with a scripted model and no network.

Each test pins one behaviour of the loop itself. The model is a fake that returns
pre-written replies in order, so every test is deterministic and runs offline.
"""

from agent_lab.agent.loop import run
from agent_lab.agent.types import ModelReply, ToolCall, Usage
from fakes import ScriptedModel


def test_a_direct_answer_ends_the_loop_after_one_step() -> None:
    model = ScriptedModel(
        [ModelReply(text="Thursday is your lightest day.", tool_calls=(), usage=Usage(10, 5))]
    )

    result = run(task="When am I free this week?", model=model, tools=[], max_steps=5)

    assert result.outcome == "completed"
    assert result.answer == "Thursday is your lightest day."
    assert len(result.steps) == 1
    assert model.calls == 1


def test_a_tool_call_is_executed_and_its_result_is_fed_back() -> None:
    from pydantic import BaseModel

    from agent_lab.agent.tools import Tool

    class AddArgs(BaseModel):
        a: int
        b: int

    calls: list[AddArgs] = []

    def add(args: AddArgs) -> int:
        calls.append(args)
        return args.a + args.b

    tool = Tool(name="add", description="Add two integers.", args_model=AddArgs, fn=add)
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="add", arguments={"a": 2, "b": 3}, id="c1"),),
                usage=Usage(20, 8),
            ),
            ModelReply(text="2 + 3 is 5.", tool_calls=(), usage=Usage(30, 6)),
        ]
    )

    result = run(task="What is 2 + 3?", model=model, tools=[tool], max_steps=5)

    assert result.outcome == "completed"
    assert result.answer == "2 + 3 is 5."
    assert calls == [AddArgs(a=2, b=3)]
    # model, tool, model: three steps, in that order
    assert [s.kind for s in result.steps] == ["model", "tool", "model"]
    # the tool's result reached the model on the second call
    second_call_messages = model.seen_messages[1]
    assert any("5" in str(m.get("content")) for m in second_call_messages)
