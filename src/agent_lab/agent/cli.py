"""`make task t="..."`: run one task end to end and print the trace as it happens.

Until a provider is configured, --script replays a JSON file of model replies, and the first
line of output says SCRIPTED MODEL so nobody mistakes a rehearsal for a result.

Every step is one JSON line, for anything that reads stdout. A policy decision is also printed
as one readable line under its JSON, and the run ends with a summary: the outcome, the steps by
kind, the decisions by outcome, the first refusal's rule, what was held for approval, and the
task ids written. Readable lines never start with "{", so a reader of the JSON lines can skip
them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agent_lab.agent.events import JsonLinesSink
from agent_lab.agent.execute import execute
from agent_lab.agent.scripted import ScriptedModel
from agent_lab.agent.types import RunResult, Step
from agent_lab.config import Settings
from agent_lab.embeddings.fake import FakeEmbedder

DECISIONS = ("allow", "refuse", "ask_override", "error")


class TracePrinter:
    """JSON lines for every step, plus one readable line for each policy decision."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._json = JsonLinesSink(stream)

    def __call__(self, step: Step) -> None:
        self._json(step)
        if step.kind == "policy":
            self._stream.write(policy_line(step) + "\n")
            self._stream.flush()


def policy_line(step: Step) -> str:
    """``  policy #6 create[0] place_slot Thu 10:29-10:30 "..." -> REFUSE focus_block (hard)``."""
    action = step.tool_input or {}
    out: dict[str, Any] = step.tool_output if isinstance(step.tool_output, dict) else {}
    what = str(action.get("action", "?"))
    if what == "place_slot":
        start = dt.datetime.fromisoformat(str(action["start"]))
        end = dt.datetime.fromisoformat(str(action["end"]))
        what += f" {start:%a %Y-%m-%d %H:%M}-{end:%H:%M} {action.get('title')!r}"
    elif what == "task_change":
        task = "new task" if action.get("task_id") is None else f"task {action['task_id']}"
        what += f" {task}" + (f" -> {action['new_status']}" if action.get("new_status") else "")
    outcome = str(out.get("outcome", "?"))
    verdict = outcome.upper()
    if outcome == "error":
        verdict += f": {out.get('error')}"
    elif outcome in ("refuse", "ask_override"):
        verdict += f" {out.get('code')} ({out.get('tier')}): {out.get('detail')}"
    elif out.get("notes"):
        verdict += " with note: " + "; ".join(str(n) for n in out["notes"])
    return f"  policy #{step.ordinal} {action.get('item', '?')} {what} -> {verdict}"


@dataclass
class Summary:
    steps: Counter[str] = field(default_factory=Counter)
    decisions: Counter[str] = field(default_factory=Counter)
    refused: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    written: list[int] = field(default_factory=list)


def summarize(result: RunResult) -> Summary:
    """Count what a run did, from its trace alone."""
    summary = Summary()
    for step in result.steps:
        summary.steps[step.kind] += 1
        out = step.tool_output if isinstance(step.tool_output, dict) else {}
        if step.kind == "policy":
            outcome = str(out.get("outcome"))
            summary.decisions[outcome] += 1
            item = (step.tool_input or {}).get("item", "?")
            if outcome == "refuse":
                summary.refused.append(f"{out.get('code')} on {item}")
            elif outcome == "ask_override":
                summary.held.append(f"{out.get('code')} on {item}")
        elif step.kind == "tool" and step.tool_name == "write_tasks":
            summary.written.extend(int(i) for i in out.get("created", []))
            summary.written.extend(int(i) for i in out.get("updated", []))
    return summary


def main(
    argv: list[str] | None = None, *, session_factory: Callable[[], Session] | None = None
) -> int:
    """Run one task. ``session_factory`` is for tests; by default it is the configured database."""
    parser = argparse.ArgumentParser(prog="agent-lab-task", description=__doc__)
    parser.add_argument("task", help="What to do, in plain words.")
    parser.add_argument("--script", type=Path, help="Replay model replies from this JSON file.")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args(argv)

    if args.script is None:
        print(
            "no model provider is configured yet; run with --script <replies.json>", file=sys.stderr
        )
        return 2

    print(f"SCRIPTED MODEL: replaying {args.script}. This is a rehearsal, not a result.")
    model = ScriptedModel.from_json(args.script)

    # Settings() is read here, not at import, so DATABASE_URL set by a test or a shell wins.
    engine = None
    if session_factory is None:
        engine = create_engine(Settings().database_url, pool_pre_ping=True)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        run, result = execute(
            task=args.task,
            model=model,
            model_name="scripted",
            session=session,
            embedder=FakeEmbedder(),
            max_steps=args.max_steps,
            on_step=TracePrinter(sys.stdout),
            now=lambda: dt.datetime.now(dt.UTC),
        )
        session.commit()
        summary = summarize(result)
        kinds = ", ".join(f"{k} {summary.steps[k]}" for k in ("model", "tool", "policy"))
        by_outcome = ", ".join(f"{d} {summary.decisions[d]}" for d in DECISIONS)
        print()
        print(
            f"outcome: {result.outcome}   run id: {run.id}   steps: {len(result.steps)} ({kinds})"
        )
        print(f"policy decisions: {sum(summary.decisions.values())} ({by_outcome})")
        print(f"refused: {', '.join(summary.refused) or 'none'}")
        print(f"first refusal on the run: {run.refusal_reason or 'none'}")
        print(f"held for approval: {', '.join(summary.held) or 'none'}")
        print(f"tasks written: {summary.written or 'none'}")
        print(f"cost: {'unknown (scripted)' if run.cost_usd is None else f'${run.cost_usd:.4f}'}")
        print(f"answer: {result.answer}")
    if engine is not None:
        engine.dispose()
    return 0 if result.outcome == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
