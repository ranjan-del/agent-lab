"""The policy gate on the write path: every proposed task write is decided before it is made.

``execute()`` builds one ``WriteGate`` per run and binds it to the ``write_tasks`` tool. Reads
(``read_calendar_window``, ``search_transcripts``) never pass through here. For each item of a
``write_tasks`` call the gate maps the item to the engine's actions, builds a ``Context`` from
the database (``policy_context.py``), asks the pure engine, records every decision as a
``policy`` step, and writes only the items every decision allowed.

How a write maps to actions:

* every create is ``TaskChange(task_id=None)`` and every update is
  ``TaskChange(task_id, new_status)``, so ``never_mark_done`` sees each one;
* a ``due_at``, on a create or an update, is ALSO a ``PlaceSlot``: a deadline is a time on my
  calendar, so the rules about placing time apply to it. The slot is the last minute before
  the deadline (``DUE_SPAN``), so a deadline at the end of working hours is inside them. Its
  title is the task text, so a task about a client call or a one-to-one is recognised by the
  same keywords as a meeting.

What each outcome does, per item (never an exception, SPEC decision 2, fail open to the model):

* ALLOW: the item is written; soft notes come back in ``notes`` for the model to say.
* REFUSE: the item is not written; ``refused`` names the rule. The first refusal of the run
  also fills ``agent_runs.refusal_reason`` and ``policy_id`` (``recorder.first_refusal``).
* ASK_OVERRIDE: the item is not written; ``awaiting_approval`` names the rules and what would
  have to be true. The question is recorded in the policy step. No approval command exists
  yet, so nothing picks the question up: the item is skipped and the run goes on.
* The engine could not decide (``IncompleteContext``, ``UnknownRule``, a naive ``due_at``):
  the item is not written, and ``policy_errors`` says why. A write nobody could check is not
  a write that passed.

One item's outcome never blocks another item in the same call: the old all-or-nothing refusal
in ``write_tasks`` is gone, and this is now the only place a write is refused.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from agent_lab.agent.policy.engine import evaluate
from agent_lab.agent.policy.rules import LoadedPolicy, UnknownRule
from agent_lab.agent.policy.types import (
    Decision,
    IncompleteContext,
    Outcome,
    PlaceSlot,
    ProposedAction,
    TaskChange,
)
from agent_lab.agent.policy_context import build_context, is_client_call, is_one_to_one
from agent_lab.agent.tools_tasks import NewTask, TaskUpdate, WriteTasksArgs, write_tasks
from agent_lab.agent.types import Step
from agent_lab.models import Task

# A deadline is an instant; the engine reasons about intervals. The minute before it stands in.
DUE_SPAN = dt.timedelta(minutes=1)


@dataclass
class _Item:
    """One item of a write_tasks call, and what the gate found out about it."""

    label: str
    blocked: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class WriteGate:
    def __init__(
        self,
        session: Session,
        policies: Sequence[LoadedPolicy],
        *,
        now: Callable[[], dt.datetime],
        tz: str,
        run_id: int | None,
    ) -> None:
        self._session = session
        self._policies = tuple(policies)
        self._now = now
        self._tz = tz
        self._run_id = run_id
        self._pending: list[Step] = []

    # ---- the loop's side of the seam ---------------------------------------------------------
    def drain(self) -> list[Step]:
        """The policy steps recorded since the last drain, for the loop to put in the trace."""
        out, self._pending = self._pending, []
        return out

    # ---- one decision ------------------------------------------------------------------------
    def decide(self, action: ProposedAction, *, item: str) -> Decision | str:
        """Decide one action and record it. A string is the reason no decision was possible."""
        t0 = time.perf_counter()
        spans = [(action.start, action.end)] if isinstance(action, PlaceSlot) else []
        task_ids = [action.task_id] if isinstance(action, TaskChange) and action.task_id else []
        verdict: Decision | str
        try:
            context = build_context(
                self._session,
                policies=self._policies,
                now=self._now(),
                tz=self._tz,
                spans=spans,
                task_ids=task_ids,
            )
            verdict = evaluate(action, context, self._policies)
        except (IncompleteContext, UnknownRule) as exc:
            verdict = f"{type(exc).__name__}: {exc}"
        self._pending.append(
            Step(
                ordinal=0,
                kind="policy",
                tool_name="write_tasks",
                tool_input={"item": item, **action_json(action)},
                tool_output=decision_json(verdict),
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        )
        return verdict

    # ---- the gated tool --------------------------------------------------------------------
    def write_tasks(self, args: WriteTasksArgs) -> dict[str, Any]:
        """Decide every item, write the ones allowed, and say what happened to each."""
        creates: list[NewTask] = []
        updates: list[TaskUpdate] = []
        items: list[_Item] = []
        for i, new in enumerate(args.create):
            item = self._review(f"create[{i}]", None, None, new.due_at, new.text)
            items.append(item)
            if not item.blocked:
                creates.append(new)
        for i, change in enumerate(args.update):
            item = self._review(
                f"update[{i}]", change.id, change.status, change.due_at, change.text
            )
            items.append(item)
            if not item.blocked:
                updates.append(change)

        written = write_tasks(
            self._session,
            WriteTasksArgs(create=creates, update=updates),
            now=self._now(),
            run_id=self._run_id,
        )
        blocked = [b for item in items for b in item.blocked]
        return {
            **written,
            "refused": [b for b in blocked if b["outcome"] == "refuse"],
            "awaiting_approval": [b for b in blocked if b["outcome"] == "ask_override"],
            "policy_errors": [b for b in blocked if b["outcome"] == "error"],
            "notes": [note for item in items for note in item.notes],
        }

    def _review(
        self,
        label: str,
        task_id: int | None,
        status: Literal["open", "dropped", "done"] | None,
        due_at: dt.datetime | None,
        text: str | None,
    ) -> _Item:
        item = _Item(label)
        change = TaskChange(task_id=task_id, new_status=status)
        actions: list[ProposedAction] = [change]
        if due_at is not None:
            if due_at.tzinfo is None or due_at.utcoffset() is None:
                item.blocked.append(_error(label, f"due_at {due_at.isoformat()} has no timezone"))
                return item
            title = text if text is not None else self._task_text(task_id)
            actions.append(
                PlaceSlot(
                    start=due_at - DUE_SPAN,
                    end=due_at,
                    title=title,
                    is_one_to_one=is_one_to_one(title),
                    is_client_call=is_client_call(title),
                )
            )
        for action in actions:
            verdict = self.decide(action, item=label)
            if isinstance(verdict, str):
                item.blocked.append(_error(label, verdict))
            elif verdict.outcome is Outcome.ALLOW:
                item.notes.extend(f"{label}: {a.note}" for a in verdict.notes)
            else:
                item.blocked.append(_blocked(label, verdict))
        return item

    def _task_text(self, task_id: int | None) -> str | None:
        task = self._session.get(Task, task_id) if task_id is not None else None
        return task.text if task is not None else None


# ---------------------------------------------------------------------------------------------
# What the trace stores and what the model reads
# ---------------------------------------------------------------------------------------------
def action_json(action: ProposedAction) -> dict[str, Any]:
    if isinstance(action, PlaceSlot):
        return {
            "action": "place_slot",
            "start": action.start.isoformat(),
            "end": action.end.isoformat(),
            "title": action.title,
            "is_one_to_one": action.is_one_to_one,
            "is_client_call": action.is_client_call,
        }
    if isinstance(action, TaskChange):
        return {"action": "task_change", "task_id": action.task_id, "new_status": action.new_status}
    return {"action": type(action).__name__}


def decision_json(verdict: Decision | str) -> dict[str, Any]:
    if isinstance(verdict, str):
        return {"outcome": "error", "error": verdict}
    return {
        "outcome": verdict.outcome.value,
        "policy_id": verdict.policy_id,
        "code": verdict.code,
        "tier": verdict.tier,
        "reason": verdict.reason,
        "detail": verdict.detail or None,
        "also": [{"policy_id": h.policy_id, "code": h.code, "tier": h.tier} for h in verdict.also],
        "needs": verdict.needs,
        "notes": [a.note for a in verdict.notes],
    }


def _blocked(label: str, decision: Decision) -> dict[str, Any]:
    codes = [c for c in [decision.code, *(h.code for h in decision.also)] if c]
    if decision.outcome is Outcome.REFUSE:
        message = (
            f"Not written. Refused by {decision.code} ({decision.tier}): {decision.reason} "
            f"{decision.detail}. A hard rule has no override; tell me which rule stopped it."
        )
    else:
        message = (
            f"Not written. Awaiting my approval under {', '.join(codes)} ({decision.tier}): "
            f"{decision.reason} {decision.detail}. {decision.needs} "
            "Carry on with the rest; tell me this one is waiting for me."
        )
    return {
        "item": label,
        "outcome": decision.outcome.value,
        "rule": decision.code,
        "rules": codes,
        "tier": decision.tier,
        "reason": decision.reason,
        "detail": decision.detail,
        "message": message,
    }


def _error(label: str, why: str) -> dict[str, Any]:
    return {
        "item": label,
        "outcome": "error",
        "error": why,
        "message": f"Not written. The policy check could not decide this item: {why}",
    }
