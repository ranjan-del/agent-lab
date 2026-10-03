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


def test_an_unknown_tool_is_reported_to_the_model_not_raised() -> None:
    """The model hallucinated a tool name. That is the model's mistake to recover from."""
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="teleport", arguments={}, id="c1"),),
                usage=Usage(20, 8),
            ),
            ModelReply(
                text="I cannot do that; here is what I can do.", tool_calls=(), usage=Usage(30, 6)
            ),
        ]
    )

    result = run(task="Teleport me to Thursday", model=model, tools=[], max_steps=5)

    assert result.outcome == "completed"
    assert result.answer == "I cannot do that; here is what I can do."
    tool_step = result.steps[1]
    assert tool_step.kind == "tool"
    assert tool_step.tool_name == "teleport"
    assert "unknown tool" in str(tool_step.tool_output).lower()
    # and the model was told, in the messages of its second call
    second = model.seen_messages[1]
    assert any("unknown tool" in str(m.get("content")).lower() for m in second)


def test_bad_arguments_are_returned_for_repair_and_the_tool_does_not_run() -> None:
    from pydantic import BaseModel

    from agent_lab.agent.tools import Tool

    class AddArgs(BaseModel):
        a: int
        b: int

    ran: list[AddArgs] = []

    def add(args: AddArgs) -> int:
        ran.append(args)
        return args.a + args.b

    tool = Tool(name="add", description="Add two integers.", args_model=AddArgs, fn=add)
    model = ScriptedModel(
        [
            # wrong: 'b' missing and 'a' is not an int
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="add", arguments={"a": "two"}, id="c1"),),
                usage=Usage(20, 8),
            ),
            # repaired on the second try
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="add", arguments={"a": 2, "b": 3}, id="c2"),),
                usage=Usage(25, 8),
            ),
            ModelReply(text="5", tool_calls=(), usage=Usage(30, 2)),
        ]
    )

    result = run(task="add two and three", model=model, tools=[tool], max_steps=6)

    assert result.outcome == "completed"
    assert result.answer == "5"
    assert ran == [AddArgs(a=2, b=3)], "the function ran only with valid arguments"
    first_tool_step = result.steps[1]
    assert "error" in first_tool_step.tool_output
    assert "b" in str(first_tool_step.tool_output), "the message names the missing field"


def test_the_step_limit_stops_a_model_that_never_finishes() -> None:
    """Every loop needs an exit that does not depend on the model behaving."""
    from pydantic import BaseModel

    from agent_lab.agent.tools import Tool

    class NoArgs(BaseModel):
        pass

    tool = Tool(name="ping", description="Does nothing.", args_model=NoArgs, fn=lambda a: "pong")
    forever = ModelReply(
        text=None, tool_calls=(ToolCall(name="ping", arguments={}, id="c"),), usage=Usage(10, 4)
    )
    model = ScriptedModel([forever] * 50)

    result = run(task="loop forever", model=model, tools=[tool], max_steps=3)

    assert result.outcome == "step_limit"
    assert result.answer is None
    assert model.calls == 3, "exactly max_steps model calls, then stop"
    assert [s.kind for s in result.steps] == ["model", "tool"] * 3


def test_a_tool_that_raises_becomes_an_error_result_not_a_crash() -> None:
    """Fail open to the model, on purpose: it can explain or try another route. The step
    limit is what stops a permanently broken tool from looping forever."""
    from pydantic import BaseModel

    from agent_lab.agent.tools import Tool

    class NoArgs(BaseModel):
        pass

    def broken(args: NoArgs) -> str:
        raise ConnectionError("database unreachable")

    tool = Tool(name="calendar", description="Read the calendar.", args_model=NoArgs, fn=broken)
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="calendar", arguments={}, id="c1"),),
                usage=Usage(10, 4),
            ),
            ModelReply(text="I could not reach the calendar.", tool_calls=(), usage=Usage(20, 6)),
        ]
    )

    result = run(task="what is on today", model=model, tools=[tool], max_steps=4)

    assert result.outcome == "completed"
    assert result.answer == "I could not reach the calendar."
    err = result.steps[1].tool_output
    assert (
        "error" in err
        and "ConnectionError" in err["error"]
        and "database unreachable" in err["error"]
    )


def test_the_system_prompt_is_the_first_message_on_every_model_call() -> None:
    """Fixed text first so a provider can cache it; the task and everything after it vary."""
    model = ScriptedModel(
        [
            ModelReply(
                text=None,
                tool_calls=(ToolCall(name="nope", arguments={}, id="c"),),
                usage=Usage(5, 1),
            ),
            ModelReply(text="ok", tool_calls=(), usage=Usage(6, 1)),
        ]
    )

    run(task="hello", model=model, tools=[], max_steps=3, system="You are the calendar agent.")

    for call in model.seen_messages:
        assert call[0] == {"role": "system", "content": "You are the calendar agent."}
        assert call[1] == {"role": "user", "content": "hello"}


def test_steps_a_tool_emits_while_running_are_recorded_before_its_own_step() -> None:
    """The policy gate decides inside the write tool; its decisions land in the trace, in
    order, before the tool step that reports what was written."""
    from pydantic import BaseModel

    from agent_lab.agent.tools import Tool
    from agent_lab.agent.types import Step

    class NoArgs(BaseModel):
        pass

    pending: list[Step] = []

    def write(_: NoArgs) -> str:
        pending.append(Step(ordinal=0, kind="policy", tool_name="write", tool_output={"n": 1}))
        pending.append(Step(ordinal=0, kind="policy", tool_name="write", tool_output={"n": 2}))
        return "written"

    def drain() -> list[Step]:
        out = list(pending)
        pending.clear()
        return out

    tool = Tool(name="write", description="", args_model=NoArgs, fn=write)
    model = ScriptedModel(
        [
            ModelReply(
                text=None, tool_calls=(ToolCall(name="write", arguments={}),), usage=Usage(1, 1)
            ),
            ModelReply(text="ok", tool_calls=(), usage=Usage(1, 1)),
        ]
    )
    seen: list[Step] = []

    result = run(
        task="write", model=model, tools=[tool], max_steps=3, on_step=seen.append, drain=drain
    )

    assert [s.kind for s in result.steps] == ["model", "policy", "policy", "tool", "model"]
    assert [s.ordinal for s in result.steps] == [1, 2, 3, 4, 5]
    assert [s.tool_output for s in result.steps[1:3]] == [{"n": 1}, {"n": 2}]
    assert seen == result.steps
