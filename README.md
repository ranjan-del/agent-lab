# agent-lab

A policy-governed workflow agent: it plans multi-step work over calendar and transcript data,
checks every proposed action against a deterministic policy engine, and produces a traced,
evaluated record of what it did and why.

Most agent demos show an agent doing things. This one is built to show an agent correctly
refusing, and to prove with numbers how often it gets that right.

## Status

Week 3 of 24. The agent runs a multi-step task end to end, and every proposed write passes a
deterministic policy gate first. The model is still scripted (see Known limits).

| Capability | State |
|---|---|
| One-command run | Working |
| Health check with dependency status | Working |
| Migrations, reproducible from zero | Working |
| Calendar ingestion (.ics) | Working |
| Transcript ingestion, chunk and embed | Working |
| Agent loop, three tools, traced to `run_steps` | Working, with a scripted model |
| Policy engine: 13 rules as rows, gate on every write | Working |
| Eval harness | Week 4 |

## Run it

```
make up        # build, start db + api, wait for health
make migrate   # apply migrations
make health    # {"status":"ok", ...}
```

Then `make check` runs lint, types and tests. `make clean` destroys the database volume.

## Gate 1: one command, a real multi-step task, through the gate

```
make gate1
```

It migrates the dev database to head, loads the demo week (`make demo`: five meetings in the
week of Mon 5 Oct 2026 and one transcript of a client call, from `tests/fixtures/demo/`,
idempotent), then runs the task CLI on `tests/fixtures/script_plan_week_gated.json`. The run
reads the calendar, searches the transcripts, and proposes three tasks whose deadlines land on
all three outcomes. Each `policy_decision` prints as a JSON event and as one readable line,
and the run ends with a summary. The recorded run, with the command at the top:
[`docs/gate1/2026-09-30-make-gate1.txt`](docs/gate1/2026-09-30-make-gate1.txt).
`tests/test_gate1.py` runs the same path on the test database and asserts the three outcomes,
so Gate 1 cannot regress silently.

## Numbers

From the recorded Gate 1 run and the suite at the same commit. Pass rate, p50 and p95 latency,
and cost per task land in weeks 5, 9 and 10, when there is a real model to measure.

| Measure | Value |
|---|---|
| Steps in the Gate 1 run | 13: model 4, tool 3, policy 6 |
| Policy decisions | 6: allow 4, refuse 1, ask_override 1, error 0 |
| Refused | `focus_block` (hard): pricing note due Thu 10:30, inside the 09:00 to 11:00 block |
| Held for approval | `working_hours` (middle): board pack review due Thu 20:00 |
| Allowed with a soft note | `client_calls_late_morning`: client call prep due Thu 15:00 |
| Tasks written | 1 of 3 proposed |
| First refusal on `agent_runs` | `focus_block`, with its policy id |
| Cost | unknown: the model is scripted |
| Tests | 151 passed (`make check`: ruff, ruff format, mypy --strict, pytest) |

## Known limits

Stated here so no number above is read as more than it is.

| Limit | Effect |
|---|---|
| The model is scripted | `make task` and `make gate1` replay JSON replies and say so on the first line. The provider adapter is still deferred, so the run proves the plumbing and the gate, not the model's judgement |
| Deadlines are counted like meetings | the gate places a `due_at` as a one-minute slot, and the count, hours and gap rules count it as a meeting |
| `read_calendar_window` free slots | still lists free slots inside the focus block, and scans weekends; the gate would refuse or hold a write placed there |
| `prefer_lightest_day` never fires | the gate builds its context with empty `candidate_days`, so the rule has nothing to compare |
| One-to-ones and client calls | recognised from title keywords only (`policy_context.py`); a weak signal in both directions |
| ASK_OVERRIDE has no approval command | a held item is not written and stays in the trace; nothing yet lists the questions or applies an approved item |
| `docs/explain/load.sql` gives every meeting the same `starts_at` | the uncorrelated LATERAL `random()` runs once (checked: 1 distinct value over 1,000 rows), so the Q2 and Q10 timings in `docs/explain/README.md` are stale until the loader is fixed and re-measured |

## Project documentation

Every flagship repository documents the same ten things. Status shows what exists today.

| Section | Document | Status |
|---|---|---|
| README | [README.md](README.md) | Written |
| Architecture | [docs/architecture.md](docs/architecture.md) | Partial |
| Design decisions | [docs/design-decisions.md](docs/design-decisions.md) | Partial |
| Benchmarks | [docs/benchmarks.md](docs/benchmarks.md) | Partial |
| Failure cases | [docs/failure-cases.md](docs/failure-cases.md) | Partial |
| Evaluation | [docs/evaluation.md](docs/evaluation.md) | Partial |
| Trade-offs | [docs/trade-offs.md](docs/trade-offs.md) | To be written |
| Deployment | [docs/deployment.md](docs/deployment.md) | Partial |
| Cost | [docs/cost.md](docs/cost.md) | Partial |
| Future work | [docs/future-work.md](docs/future-work.md) | Partial |

