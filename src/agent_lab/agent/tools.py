"""A tool is a name, a description the model reads, an argument schema, and a function."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[Any], Any]

    def call(self, arguments: dict[str, Any]) -> Any:
        """Validate the model's arguments against the schema, then run."""
        return self.fn(self.args_model.model_validate(arguments))
