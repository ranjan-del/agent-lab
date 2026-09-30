"""The loop: ask the model; run any tools it asked for; feed the results back; repeat."""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import ValidationError

from agent_lab.agent.tools import Tool
from agent_lab.agent.types import ModelClient, RunResult, Step, ToolCall


def run(
    task: str,
    model: ModelClient,
    tools: list[Tool],
    max_steps: int,
    on_step: Callable[[Step], None] | None = None,
    system: str | None = None,
    drain: Callable[[], Sequence[Step]] | None = None,
) -> RunResult:
    """Run until the model answers or ``max_steps`` model turns are spent.

    ``drain``, when given, is called after every tool call and returns the steps the tool
    emitted while it ran (the policy gate's decisions). They are numbered and recorded before
    the tool's own step, so the trace reads in the order things happened.
    """
    by_name = {t.name: t for t in tools}
    # The system prompt goes first and never changes within a run, so a provider that caches
    # prompt prefixes can reuse it on every turn. Everything that varies comes after.
    messages: list[dict[str, Any]] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task})
    steps: list[Step] = []

    for _ in range(max_steps):
        t0 = time.perf_counter()
        reply = model.complete(messages, tools)
        _record(
            steps,
            on_step,
            Step(
                ordinal=len(steps) + 1,
                kind="model",
                usage=reply.usage,
                text=reply.text,
                latency_ms=_ms_since(t0),
            ),
        )
        if not reply.tool_calls:
            return RunResult(outcome="completed", answer=reply.text, steps=steps)

        messages.append(
            {"role": "assistant", "content": reply.text, "tool_calls": list(reply.tool_calls)}
        )
        for call in reply.tool_calls:
            t0 = time.perf_counter()
            output = _invoke(by_name, call)
            for emitted in drain() if drain is not None else ():
                _record(steps, on_step, dataclasses.replace(emitted, ordinal=len(steps) + 1))
            _record(
                steps,
                on_step,
                Step(
                    ordinal=len(steps) + 1,
                    kind="tool",
                    tool_name=call.name,
                    tool_input=call.arguments,
                    tool_output=output,
                    latency_ms=_ms_since(t0),
                ),
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(output),
                }
            )

    # The model asked for another tool on its last allowed turn. Stop without an answer and
    # say so, rather than pretending the last text was one.
    return RunResult(outcome="step_limit", answer=None, steps=steps)


def _invoke(by_name: dict[str, Tool], call: ToolCall) -> Any:
    """Run one tool call. Every failure becomes a result the model can read and act on.

    Nothing here raises: an unknown name or bad arguments is the model's mistake, and the
    model is the one that can repair it. A step limit bounds how long that can go on.
    """
    tool = by_name.get(call.name)
    if tool is None:
        return {"error": f"unknown tool {call.name!r}; available: {sorted(by_name)}"}
    try:
        return tool.call(call.arguments)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return {"error": f"invalid arguments for {call.name!r}: " + "; ".join(problems)}
    except Exception as exc:  # noqa: BLE001 - deliberately fail open to the model; see docstring
        return {"error": f"{call.name!r} failed: {type(exc).__name__}: {exc}"}


def _record(steps: list[Step], on_step: Callable[[Step], None] | None, step: Step) -> None:
    """Append, then report. The trace is written before anyone else sees the step."""
    steps.append(step)
    if on_step is not None:
        on_step(step)


def _ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
