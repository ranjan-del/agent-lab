"""Write a finished run to agent_runs and run_steps, priced.

The trace is the product of a run as much as the answer is: week 4's evals, week 9's tracing
and week 10's cost model all read these two tables. Nothing is recorded that the loop did not
observe, and nothing the loop observed is dropped.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from agent_lab.agent.types import RunResult, Step
from agent_lab.models import AgentRun, RunStep

# USD per million tokens (input, output), read from the vendor's pricing page on 2026-09-05.
# See docs/notes/2026-09-05-tokens-context-cost.md. A model not listed here costs None, which
# is honest: an unknown price is not zero.
PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
    "claude-opus-5": (Decimal("5"), Decimal("25")),
}


def cost_usd(steps: list[Step], model_name: str) -> Decimal | None:
    prices = PRICES_PER_MTOK.get(model_name)
    if prices is None:
        return None
    tokens_in = sum(s.usage.input_tokens for s in steps if s.usage)
    tokens_out = sum(s.usage.output_tokens for s in steps if s.usage)
    million = Decimal(1_000_000)
    return (tokens_in * prices[0] + tokens_out * prices[1]) / million


def record_run(
    session: Session,
    *,
    task: str,
    result: RunResult,
    model_name: str,
    started_at: dt.datetime,
    finished_at: dt.datetime,
) -> AgentRun:
    run = AgentRun(
        task=task,
        started_at=started_at,
        finished_at=finished_at,
        outcome=result.outcome,
        cost_usd=cost_usd(result.steps, model_name),
        latency_ms=int((finished_at - started_at).total_seconds() * 1000),
    )
    session.add(run)
    session.flush()
    for step in result.steps:
        session.add(
            RunStep(
                run_id=run.id,
                ordinal=step.ordinal,
                kind=step.kind,
                tool_name=step.tool_name,
                input=_jsonable(step.tool_input) if step.kind == "tool" else {},
                output=_jsonable(step.tool_output) if step.kind == "tool" else {"text": step.text},
                tokens_in=step.usage.input_tokens if step.usage else None,
                tokens_out=step.usage.output_tokens if step.usage else None,
                latency_ms=step.latency_ms,
            )
        )
    session.flush()
    return run


def _jsonable(value: Any) -> dict[str, Any]:
    """JSONB wants a dict; a tool may return a list or a scalar. Wrap, never drop."""
    if value is None:
        return {}
    if isinstance(value, dict):
        rendered: dict[str, Any] = json.loads(json.dumps(value, default=str))
        return rendered
    return {"value": json.loads(json.dumps(value, default=str))}
