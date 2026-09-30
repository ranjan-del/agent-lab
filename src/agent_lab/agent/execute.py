"""One entry point: run a task with the three tools against the database, and record it."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from sqlalchemy.orm import Session

from agent_lab.agent.loop import run as run_loop
from agent_lab.agent.policy.rules import load_policies
from agent_lab.agent.prompt import system_prompt
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
    # Loaded before the run opens: a policy table that does not validate stops the run here,
    # rather than letting the agent run without rules.
    policies = load_policies(session)
    run = start_run(session, task=task, started_at=now())
    tools = build_tools(session, embedder, now=now, run_id=run.id, policies=policies)
    result = run_loop(
        task=task,
        model=model,
        tools=tools,
        max_steps=max_steps,
        on_step=on_step,
        system=system_prompt(now=now(), policies=policies),
    )
    finish_run(session, run, result=result, model_name=model_name, finished_at=now())
    return run, result
