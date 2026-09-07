"""One entry point: run a task with the three tools against the database, and record it."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from sqlalchemy.orm import Session

from agent_lab.agent.loop import run as run_loop
from agent_lab.agent.recorder import finish_run, start_run
from agent_lab.agent.toolkit import build_tools
from agent_lab.agent.types import ModelClient, RunResult, Step
from agent_lab.embeddings.base import Embedder
from agent_lab.models import AgentRun


def execute(
    *,
    task: str,
    model: ModelClient,
    model_name: str,
    session: Session,
    embedder: Embedder,
    max_steps: int = 8,
    on_step: Callable[[Step], None] | None = None,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> tuple[AgentRun, RunResult]:
    run = start_run(session, task=task, started_at=now())
    tools = build_tools(session, embedder, now=now, run_id=run.id)
    result = run_loop(task=task, model=model, tools=tools, max_steps=max_steps, on_step=on_step)
    finish_run(session, run, result=result, model_name=model_name, finished_at=now())
    return run, result
