"""`make task t="..."`: run one task end to end and print the trace as it happens.

Until a provider is configured, --script replays a JSON file of model replies, and the first
line of output says SCRIPTED MODEL so nobody mistakes a rehearsal for a result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent_lab.agent.events import JsonLinesSink
from agent_lab.agent.execute import execute
from agent_lab.agent.scripted import ScriptedModel
from agent_lab.config import Settings
from agent_lab.embeddings.fake import FakeEmbedder


def main(argv: list[str] | None = None) -> int:
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
    engine = create_engine(Settings().database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        run, result = execute(
            task=args.task,
            model=model,
            model_name="scripted",
            session=session,
            embedder=FakeEmbedder(),
            max_steps=args.max_steps,
            on_step=JsonLinesSink(sys.stdout),
            now=lambda: dt.datetime.now(dt.UTC),
        )
        session.commit()
        print()
        print(f"outcome: {result.outcome}   run id: {run.id}   steps: {len(result.steps)}")
        print(f"cost: {'unknown (scripted)' if run.cost_usd is None else f'${run.cost_usd:.4f}'}")
        print(f"answer: {result.answer}")
    engine.dispose()
    return 0 if result.outcome == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
