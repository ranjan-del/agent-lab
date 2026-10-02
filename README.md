# agent-lab

A policy-governed workflow agent: it plans multi-step work over calendar and transcript data,
checks every proposed action against a deterministic policy engine, and produces a traced,
evaluated record of what it did and why.

Most agent demos show an agent doing things. This one is built to show an agent correctly
refusing, and to prove with numbers how often it gets that right.

## Status

Week 1 of 24. The skeleton runs; the agent does not exist yet.

| Capability | State |
|---|---|
| One-command run | Working |
| Health check with dependency status | Working |
| Migrations, reproducible from zero | Working |
| Schema (9 tables) | Wednesday |
| Calendar ingestion | Thursday |
| Transcript ingestion, chunk and embed | Friday |
| Agent loop | Week 2 |
| Policy engine | Week 3 |
| Eval harness | Week 4 |

## Run it

```
make up        # build, start db + api, wait for health
make migrate   # apply migrations
make health    # {"status":"ok", ...}
```

Then `make check` runs lint, types and tests. `make clean` destroys the database volume.

## Numbers

Empty on purpose. Nothing ships here without a number, and there is nothing to measure yet.
Pass rate, p50 and p95 latency, and cost per task land in weeks 5, 9 and 10.

## Project documentation

Every flagship repository documents the same ten things. Status shows what exists today.

| Section | Document | Status |
|---|---|---|
| README | [README.md](README.md) | Written |
| Architecture | [docs/architecture.md](docs/architecture.md) | Partial |
| Design decisions | [docs/design-decisions.md](docs/design-decisions.md) | Partial |
| Benchmarks | [docs/benchmarks.md](docs/benchmarks.md) | Partial |
| Failure cases | [docs/failure-cases.md](docs/failure-cases.md) | To be written |
| Evaluation | [docs/evaluation.md](docs/evaluation.md) | Partial |
| Trade-offs | [docs/trade-offs.md](docs/trade-offs.md) | To be written |
| Deployment | [docs/deployment.md](docs/deployment.md) | Partial |
| Cost | [docs/cost.md](docs/cost.md) | Partial |
| Future work | [docs/future-work.md](docs/future-work.md) | Partial |

