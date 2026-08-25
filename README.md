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
