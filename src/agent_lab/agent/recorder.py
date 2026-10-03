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


def start_run(session: Session, *, task: str, started_at: dt.datetime) -> AgentRun:
    """Open the run row before the loop starts, so tools can stamp what they write with its id."""
    run = AgentRun(task=task, started_at=started_at)
    session.add(run)
    session.flush()
    return run


def finish_run(
    session: Session,
    run: AgentRun,
    *,
    result: RunResult,
    model_name: str,
    finished_at: dt.datetime,
) -> AgentRun:
    run.finished_at = finished_at
    run.outcome = result.outcome
    cost = cost_usd(result.steps, model_name)
    run.cost_usd = None if cost is None else float(cost)
    run.latency_ms = int((finished_at - run.started_at).total_seconds() * 1000)
    refusal = first_refusal(result.steps)
    if refusal is not None:
        run.policy_id, run.refusal_reason = refusal
    for step in result.steps:
        session.add(
            RunStep(
                run_id=run.id,
                ordinal=step.ordinal,
                kind=step.kind,
                tool_name=step.tool_name,
                input=_jsonable(step.tool_input) if step.kind != "model" else {},
                output=(
                    _jsonable(step.tool_output) if step.kind != "model" else {"text": step.text}
                ),
                tokens_in=step.usage.input_tokens if step.usage else None,
                tokens_out=step.usage.output_tokens if step.usage else None,
                latency_ms=step.latency_ms,
            )
        )
    session.flush()
    return run


def first_refusal(steps: list[Step]) -> tuple[int | None, str] | None:
    """(policy_id, reason) of the first REFUSE the gate recorded, or None.

    The first, because it is the one the rest of the run reacted to. An ask is not a refusal:
    it is held for approval, and the run's refusal columns stay empty for it. Every decision,
    including later refusals, stays in run_steps.
    """
    for step in steps:
        out = step.tool_output
        if step.kind == "policy" and isinstance(out, dict) and out.get("outcome") == "refuse":
            reason = f"{out.get('code')}: {out.get('reason', '')} {out.get('detail', '')}".strip()
            policy_id = out.get("policy_id")
            return (policy_id if isinstance(policy_id, int) else None), reason
    return None


def record_run(
    session: Session,
    *,
    task: str,
    result: RunResult,
    model_name: str,
    started_at: dt.datetime,
    finished_at: dt.datetime,
) -> AgentRun:
    """Write a run that already finished. execute() uses start_run and finish_run instead."""
    run = start_run(session, task=task, started_at=started_at)
    return finish_run(session, run, result=result, model_name=model_name, finished_at=finished_at)


def _jsonable(value: Any) -> dict[str, Any]:
    """JSONB wants a dict; a tool may return a list or a scalar. Wrap, never drop."""
    if value is None:
        return {}
    if isinstance(value, dict):
        rendered: dict[str, Any] = json.loads(json.dumps(value, default=str))
        return rendered
    return {"value": json.loads(json.dumps(value, default=str))}
