"""The vocabulary the loop speaks. Nothing provider-specific lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass(frozen=True)
class ModelReply:
    """One answer from the model: text, or tool calls, or both, plus what it cost."""

    text: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage


@dataclass(frozen=True)
class Step:
    ordinal: int
    kind: Literal["model", "tool"]
    usage: Usage | None = None
    text: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any = None


Outcome = Literal["completed", "step_limit", "error"]


@dataclass
class RunResult:
    outcome: Outcome
    answer: str | None
    steps: list[Step] = field(default_factory=list)


class ModelClient(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[Any]) -> ModelReply: ...
