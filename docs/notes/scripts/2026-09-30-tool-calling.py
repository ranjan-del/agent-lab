"""Week 3 fundamentals demonstrations: tool calling, structured output, repair.

Every table in docs/notes/2026-09-30-tool-calling-structured-output.md comes out of this file.
It drives this repo's own toolkit, loop and policy engine. No Postgres, no network, no API key:
the database is replaced by two tiny stand-ins (one that returns nothing, one that is down),
and the model is either the repo's ScriptedModel or a model that never stops asking.

    uv run python docs/notes/scripts/2026-09-30-tool-calling.py

Nothing under src/ is changed. Where the note compares with behaviour src/ no longer has
(``extra="ignore"`` before caf36c3), the script rebuilds the old behaviour on a subclass
defined here, so the before and after are both measured, not remembered.

Updated after Phase 5 (f0593a3): ``build_tools`` now takes the loaded policy rows and the
run's ``WriteGate`` instead of ``now`` and ``run_id``, and "never mark done" is refused by
the gate, not by ``write_tasks``. The policy rows are the migration 0006 seed, read from the
migration file itself, so the script cannot drift from the seeded numbers.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from agent_lab.agent.gate import WriteGate
from agent_lab.agent.loop import _invoke, run
from agent_lab.agent.policy.engine import evaluate
from agent_lab.agent.policy.rules import LoadedPolicy, parse_rule
from agent_lab.agent.policy.types import (
    ChangeMeeting,
    Context,
    Decision,
    IncompleteContext,
    PlaceSlot,
    TaskChange,
)
from agent_lab.agent.scripted import ScriptedModel
from agent_lab.agent.toolkit import build_tools
from agent_lab.agent.tools import Tool
from agent_lab.agent.tools_tasks import WriteTasksArgs
from agent_lab.agent.types import ModelReply, RunResult, ToolCall, Usage
from agent_lab.embeddings.fake import FakeEmbedder

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 9, 30, 12, 0, tzinfo=IST)


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def short(value: Any, width: int = 96) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= width else text[: width - 3] + "..."


# ------------------------------------------------------------------ database stand-ins
class _NoRows:
    """What session.execute returns when the tables are empty: iterable, with .scalars()."""

    def __iter__(self) -> Any:
        return iter(())

    def scalars(self) -> list[Any]:
        return []


class EmptySession:
    """A database with no rows. Enough for the two read tools and for the gate's context.

    ``add`` and ``flush`` stand in for an insert: a flushed object gets the next id, so an
    allowed write through the gate reports a created id the way Postgres would.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []

    def execute(self, *args: object, **kwargs: object) -> _NoRows:
        return _NoRows()

    def get(self, *args: object, **kwargs: object) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        for i, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = i


class DownSession:
    """A database that is unreachable. Every query raises, as psycopg would."""

    def execute(self, *args: object, **kwargs: object) -> Any:
        raise ConnectionError("connection to server at localhost:5432 refused")


def _seed_rows() -> list[tuple[str, str, str, str, dict[str, Any]]]:
    """The thirteen rows migration 0006 seeds, read from the migration file itself."""
    root = Path(__file__).resolve().parents[3]
    path = root / "migrations/versions/0006_seed_policies.py"
    spec = importlib.util.spec_from_file_location("seed_0006_notes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seed: list[tuple[str, str, str, str, dict[str, Any]]] = module.SEED
    return seed


SEEDED = tuple(
    LoadedPolicy(
        id=i,
        code=code,
        tier=cast(Any, kind),
        description=description,
        params=parse_rule(code, rule_json),
        active=True,
    )
    for i, (code, kind, _severity, description, rule_json) in enumerate(_seed_rows(), start=1)
)


def toolkit(session: object) -> list[Tool]:
    """The real toolkit, exactly as execute() builds it: seeded rows, one gate per run."""
    db = cast(Session, session)
    gate = WriteGate(db, SEEDED, now=lambda: NOW, tz="Asia/Kolkata", run_id=None)
    return build_tools(db, FakeEmbedder(), policies=SEEDED, gate=gate)


# ------------------------------------------------------ 1. what the model is shown
rule("1. What the model is shown for each tool: name, description, input schema")

UNSUPPORTED_BY_STRICT = {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum"}


def constraints(schema: Any, found: set[str]) -> set[str]:
    """Every JSON Schema keyword in the tree that strict mode cannot enforce."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in UNSUPPORTED_BY_STRICT:
                found.add(key)
            constraints(value, found)
    elif isinstance(schema, list):
        for item in schema:
            constraints(item, found)
    return found


def validators(model: type[BaseModel]) -> list[str]:
    """Python-only checks: they run on validation but never appear in the schema."""
    infos = model.__pydantic_decorators__
    names = [*infos.field_validators, *infos.model_validators]
    return sorted(names)


print(
    f"{'tool':22s} {'desc words':>10s} {'props':>5s} {'required':18s} "
    f"{'addlProps':>9s} {'strict cannot enforce':24s} python-only validators"
)
for tool in toolkit(EmptySession()):
    schema = tool.args_model.model_json_schema()
    words = len(tool.description.split())
    required = ",".join(schema.get("required", [])) or "(none)"
    addl = str(schema.get("additionalProperties", "absent"))
    unenforced = ",".join(sorted(constraints(schema, set()))) or "-"
    python_only = ",".join(validators(tool.args_model)) or "-"
    print(
        f"{tool.name:22s} {words:10d} {len(schema['properties']):5d} {required:18s} "
        f"{addl:>9s} {unenforced:24s} {python_only}"
    )

print("\nsearch_transcripts input_schema, exactly as pydantic emits it:")
search = next(t for t in toolkit(EmptySession()) if t.name == "search_transcripts")
print(json.dumps(search.args_model.model_json_schema(), indent=2))


# ------------------------------------------- 2. malformed calls become readable results
rule("2. Malformed calls through loop._invoke: raised, or a result the model can read?")

aware = {"start": "2026-10-01T10:00:00+05:30", "end": "2026-10-02T10:00:00+05:30"}
CASES: list[tuple[str, object, ToolCall]] = [
    ("unknown tool name", EmptySession(), ToolCall("read_calendar", aware)),
    ("missing required arg", EmptySession(), ToolCall("read_calendar_window", {"start": "x"})),
    (
        "naive datetime",
        EmptySession(),
        ToolCall(
            "read_calendar_window",
            {"start": "2026-10-01T10:00:00", "end": "2026-10-02T10:00:00"},
        ),
    ),
    (
        "end before start",
        EmptySession(),
        ToolCall("read_calendar_window", {"start": aware["end"], "end": aware["start"]}),
    ),
    ("wrong type", EmptySession(), ToolCall("search_transcripts", {"query": "x", "limit": "five"})),
    ("out of range", EmptySession(), ToolCall("search_transcripts", {"query": "x", "limit": 50})),
    (
        "enum miss",
        EmptySession(),
        ToolCall("write_tasks", {"create": [{"text": "send deck", "urgency": "urgent"}]}),
    ),
    (
        "gate refusal (done)",
        EmptySession(),
        ToolCall("write_tasks", {"update": [{"id": 7, "status": "done"}]}),
    ),
    ("tool raises", DownSession(), ToolCall("search_transcripts", {"query": "pricing"})),
    (
        "misspelt key",
        EmptySession(),
        ToolCall("write_tasks", {"creates": [{"text": "send deck", "urgency": "hard"}]}),
    ),
    ("valid call", EmptySession(), ToolCall("read_calendar_window", aware)),
]


def flagged(output: Any) -> str:
    """'error' for a tool error, 'refused'/'held' for a gate outcome, 'no' for success."""
    if not isinstance(output, dict):
        return "no"
    if "error" in output:
        return "error"
    if output.get("refused"):
        return "refused"
    if output.get("awaiting_approval"):
        return "held"
    return "no"


print(f"{'case':22s} {'raised':6s} {'flagged':7s}  what the model reads")
results: dict[str, Any] = {}
for label, session, call in CASES:
    by_name = {t.name: t for t in toolkit(session)}
    try:
        output = _invoke(by_name, call)
        raised = "no"
    except Exception as exc:  # noqa: BLE001 - the point is to show whether anything escapes
        output = f"{type(exc).__name__}: {exc}"
        raised = "YES"
    results[label] = output
    print(f"{label:22s} {raised:6s} {flagged(output):7s}  {short(output)}")

print("\nFull text of four of them, as the model would read it:")
for label in ("unknown tool name", "naive datetime", "tool raises", "misspelt key"):
    print(f"  {label}: {json.dumps(results[label])}")
print("\nThe gate's refusal of 'done', as the model reads it:")
print(f"  {json.dumps(results['gate refusal (done)']['refused'][0]['message'])}")


# ------------------------------------------------- 3. the misspelt key, before and after
rule("3. The misspelt key: extra='ignore' before caf36c3 vs extra='forbid' now")


class IgnoreWriteTasksArgs(WriteTasksArgs):
    """The old behaviour, rebuilt here on a subclass. src/ now forbids unknown keys."""

    model_config = ConfigDict(extra="ignore")


bad = {"creates": [{"text": "send deck", "urgency": "hard"}]}
before = IgnoreWriteTasksArgs.model_validate(bad)
print(f"before : validates, create={before.create}, update={before.update}  (the key vanished)")
try:
    WriteTasksArgs.model_validate(bad)
except ValidationError as exc:
    problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    print(f"now    : invalid arguments for 'write_tasks': {'; '.join(problems)}")
print(
    "schema additionalProperties, before vs now: "
    f"{IgnoreWriteTasksArgs.model_json_schema().get('additionalProperties', 'absent')} vs "
    f"{WriteTasksArgs.model_json_schema().get('additionalProperties', 'absent')}"
)

print("\nEvery tool's argument model now, sent one unknown key alongside a valid call:")
VALID: dict[str, dict[str, Any]] = {
    "read_calendar_window": dict(aware),
    "search_transcripts": {"query": "pricing"},
    "write_tasks": {},
}
for tool in toolkit(EmptySession()):
    try:
        tool.args_model.model_validate({**VALID[tool.name], "bogus": 1})
        verdict = "accepted, 'bogus' silently dropped"
    except ValidationError:
        verdict = "rejected: bogus: Extra inputs are not permitted"
    print(f"  {tool.name:22s} {verdict}")


# --------------------------------------------- 4. repair: fail open vs fail closed
rule("4. One bad call, then a corrected one: fail open vs fail closed")


def reply(*calls: ToolCall, text: str | None = None) -> ModelReply:
    return ModelReply(text=text, tool_calls=calls, usage=Usage(0, 0))


def repair_script() -> list[ModelReply]:
    naive = {"start": "2026-10-01T10:00:00", "end": "2026-10-02T10:00:00"}
    return [
        reply(ToolCall("read_calendar_window", naive, id="c1")),
        reply(ToolCall("read_calendar_window", aware, id="c2")),
        reply(text="Thursday 1 October is clear from 10:00 to 19:00."),
    ]


def fail_closed_run(model: ScriptedModel, tools: list[Tool], max_steps: int) -> str:
    """The rejected alternative, written here for comparison: the same loop, no catch."""
    by_name = {t.name: t for t in tools}
    messages: list[dict[str, Any]] = [{"role": "user", "content": "When am I free Thursday?"}]
    for step in range(1, max_steps + 1):
        answer = model.complete(messages, tools)
        if not answer.tool_calls:
            return f"completed: {answer.text}"
        for call in answer.tool_calls:
            try:
                by_name[call.name].call(call.arguments)
            except Exception as exc:  # noqa: BLE001 - reporting how the run died
                return f"dead at model call {step}: {type(exc).__name__}"
    return "step_limit"


open_model = ScriptedModel(repair_script())
opened = run("When am I free Thursday?", open_model, toolkit(EmptySession()), max_steps=8)
closed_model = ScriptedModel(repair_script())
closed = fail_closed_run(closed_model, toolkit(EmptySession()), max_steps=8)

print(f"{'strategy':12s} {'outcome':38s} {'model calls':>11s} {'steps':>5s}  answer")
print(
    f"{'fail open':12s} {opened.outcome:38s} {open_model.calls:11d} "
    f"{len(opened.steps):5d}  {opened.answer}"
)
print(f"{'fail closed':12s} {closed:38s} {closed_model.calls:11d} {'-':>5s}  None")
print("\nWhat the fail-open model read before its second call:")
print(f"  {open_model.seen_messages[1][-1]['content']}")


# --------------------------------------- 5. the misspelt key through a full run
rule("5. The misspelt key through the fail-open loop: now an error the model can repair")

good = {"create": [{"text": "send deck", "urgency": "hard"}]}
repair_session = EmptySession()
repair_model = ScriptedModel(
    [
        reply(ToolCall("write_tasks", bad, id="w1")),
        reply(ToolCall("write_tasks", good, id="w2")),
        reply(text="Saved: send deck, urgency hard."),
    ]
)
repaired = run("Log that I owe the deck", repair_model, toolkit(repair_session), max_steps=8)
tool_steps = [s for s in repaired.steps if s.kind == "tool"]
print(f"outcome      : {repaired.outcome}")
print(f"first call   : {short(tool_steps[0].tool_output, 110)}")
print(f"second call  : {tool_steps[1].tool_output}")
print(f"answer       : {repaired.answer}")
print(f"rows written : {len(repair_session.added)}")
print(
    "before caf36c3 the first call validated as create=[] and returned "
    "{'created': [], 'updated': []}: completed, 'Saved', 0 rows, and no error to repair"
)


# ------------------------------------ 6. the step limit bounds a permanently broken tool
rule("6. A permanently broken tool, bounded only by max_steps")


class NeverStops:
    """A model that asks for the same tool forever, however often it fails."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[Any]) -> ModelReply:
        self.calls += 1
        call = ToolCall("search_transcripts", {"query": "pricing"}, id=f"s{self.calls}")
        return reply(call)


print(f"{'max_steps':>9s} {'outcome':12s} {'model calls':>11s} {'tool errors':>11s} {'steps':>5s}")
bounded: RunResult | None = None
for limit in (1, 3, 8, 20):
    model = NeverStops()
    bounded = run("What did we agree on pricing?", model, toolkit(DownSession()), max_steps=limit)
    errors = sum(
        1
        for s in bounded.steps
        if s.kind == "tool" and isinstance(s.tool_output, dict) and "error" in s.tool_output
    )
    print(
        f"{limit:9d} {bounded.outcome:12s} {model.calls:11d} {errors:11d} {len(bounded.steps):5d}"
    )
assert bounded is not None
print(f"answer on step_limit: {bounded.answer}")


# ------------------------------------------ 7. a policy refusal is also a readable result
rule("7. The policy gate: a refusal is a Decision the model can read, not an exception")

policies = list(SEEDED)
context = Context(now=NOW, tz="Asia/Kolkata")


def as_tool_result(decision: Decision) -> dict[str, Any]:
    """The engine's Decision, condensed. The gate's own rendering follows the table."""
    if decision.outcome == "allow":
        return {"allowed": True, "notes": [n.note for n in decision.notes]}
    return {
        "error": f"{decision.outcome} by {decision.code} ({decision.tier}): {decision.reason}",
        "detail": decision.detail,
        "also": [h.code for h in decision.also],
        "needs": decision.needs,
    }


thursday = dt.date(2026, 10, 1)
ACTIONS: list[tuple[str, Any]] = [
    (
        "slot 09:30-10:00 Thu",
        PlaceSlot(
            start=dt.datetime.combine(thursday, dt.time(9, 30), IST),
            end=dt.datetime.combine(thursday, dt.time(10, 0), IST),
        ),
    ),
    ("mark task 7 done", TaskChange(task_id=7, new_status="done")),
    (
        "slot 18:30-19:30 Thu",
        PlaceSlot(
            start=dt.datetime.combine(thursday, dt.time(18, 30), IST),
            end=dt.datetime.combine(thursday, dt.time(19, 30), IST),
        ),
    ),
    (
        "slot 14:00-15:00 Thu",
        PlaceSlot(
            start=dt.datetime.combine(thursday, dt.time(14, 0), IST),
            end=dt.datetime.combine(thursday, dt.time(15, 0), IST),
        ),
    ),
    (
        "move meeting 99",
        ChangeMeeting(
            meeting_id=99,
            new_start=dt.datetime.combine(thursday, dt.time(15, 0), IST),
            new_end=dt.datetime.combine(thursday, dt.time(15, 30), IST),
        ),
    ),
]
print(f"{'proposed action':22s} {'outcome':13s} {'rule':18s} what the model would read")
for label, action in ACTIONS:
    try:
        decision = evaluate(action, context, policies)
        print(
            f"{label:22s} {decision.outcome.value:13s} {decision.code or '-':18s} "
            f"{short(as_tool_result(decision), 80)}"
        )
    except IncompleteContext as exc:
        print(f"{label:22s} {'RAISES':13s} {'-':18s} IncompleteContext: {short(str(exc), 62)}")

print("\nThe first refusal in full, as the model would read it:")
print(json.dumps(as_tool_result(evaluate(ACTIONS[0][1], context, policies)), indent=2))

print("\nThe same kinds of write through the real gate (WriteGate.write_tasks), as the model reads")
print("them. A due_at is placed as the minute before the deadline (gate.DUE_SPAN):")
GATED: list[tuple[str, dict[str, Any]]] = [
    (
        "due Thu 10:00 (focus)",
        {"create": [{"text": "send deck", "due_at": "2026-10-01T10:00:00+05:30"}]},
    ),
    ("mark task 7 done", {"update": [{"id": 7, "status": "done"}]}),
    (
        "due Thu 20:00 (late)",
        {"create": [{"text": "send deck", "due_at": "2026-10-01T20:00:00+05:30"}]},
    ),
    (
        "due Thu 15:00 client",
        {"create": [{"text": "prep the client call", "due_at": "2026-10-01T15:00:00+05:30"}]},
    ),
]
print(f"{'proposed write':22s} {'created':7s} {'refused':18s} {'held':14s} notes")
for label, arguments in GATED:
    out = _invoke({t.name: t for t in toolkit(EmptySession())}, ToolCall("write_tasks", arguments))
    refused = ",".join(r["rule"] for r in out["refused"]) or "-"
    held = ",".join(",".join(h["rules"]) for h in out["awaiting_approval"]) or "-"
    notes = short("; ".join(out["notes"]), 60) or "-"
    print(f"{label:22s} {out['created']!s:7s} {refused:18s} {held:14s} {notes}")
