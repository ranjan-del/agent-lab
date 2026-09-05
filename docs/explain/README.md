# Query drill: 100k meetings, ten questions, one index, one rewrite

Week 1, module 4. Load synthetic rows, ask the questions the policy agent will ask, find the
slow ones, fix them, and commit the plans so the reasoning can be checked later.

Setup: `docs/explain/load.sql` into a scratch database (`agentlab_drill`) migrated to head.
2,000 people, 100,000 meetings spread over 2025 to 2026, 299,844 attendees. Postgres 16.15,
`EXPLAIN (ANALYZE, BUFFERS)`, warm cache, single run each, so treat the milliseconds as
relative, not absolute.

## The ten questions (`queries.sql`), before any change

| # | Question | ms | Plan on `meetings` |
|---|---|---|---|
| Q1 | Meetings overlapping one person's focus block | 0.6 | index (person filter narrows first) |
| Q2 | Every meeting overlapping a window | 11.0 | **seq scan**, 100k rows filtered to 25 |
| Q3 | A person's most recent meeting, via a window function | **166.2** | **seq scan**, ranks all 300k attendee rows, spills to disk |
| Q4 | Meetings a person organised in a quarter | 5.3 | index |
| Q5 | External meetings per week | 10.6 | seq scan, legitimately: it reads 1/7 of the table |
| Q6 | Title search with `ILIKE '%...%'` | 56.1 | **seq scan**, no index can serve a leading wildcard without pg_trgm |
| Q7 | Who is in this meeting | 0.05 | index |
| Q8 | Trainees with no meeting in 30 days | 9.1 | index anti-join |
| Q9 | One series' occurrences | 0.04 | index |
| Q10 | Meetings running at an instant | 10.2 | **seq scan**, same shape as Q2 |

## What was fixed, and how

| Query | Fix | Before | After | Plans |
|---|---|---|---|---|
| Q2 | GiST index over `tstzrange(starts_at, ends_at, '[)')` (migration 0004) and the query written with `&&` | 11.0 ms, 3,311 buffers | 0.19 ms, 28 buffers | `before/q2.txt`, `after/q2-range-form.txt` |
| Q10 | Same index, query written with `@>` | 10.2 ms | 0.08 ms, 5 buffers | `before/q10.txt`, `after/q10-range-form.txt` |
| Q3 | No index. Rewrite: collect the person's meetings first (`MATERIALIZED` CTE), then sort that small set | 166 ms, temp files | 0.5 ms, 287 buffers | `before/q3.txt`, `after/q3-rewrite.txt` |
| Q6 | Deferred: needs `CREATE EXTENSION pg_trgm` plus a GIN trigram index, a separate decision | 56 ms | | `before/q6.txt` |

## Three things worth remembering

1. **An expression index is only used by queries written with that expression.** With the
   GiST index in place, Q2 in its original `starts_at < :to AND ends_at > :from` form still
   sequential-scans (`after/q2-original-form-still-seq-scans.txt`, 9.8 ms). The index and the
   query shape ship together, or the index is dead weight.
2. **The slowest query was not an indexing problem.** Q3 ranked every attendee of every
   person before filtering to one person. No index fixes an algorithm that does 300k units
   of work to answer a 56-row question. The first rewrite (`ORDER BY ... LIMIT 1` over a
   backward index scan) was 16 ms because matches are sparse and the planner walked ~700
   meetings to find one; forcing the person filter first with a `MATERIALIZED` CTE made it
   0.5 ms. Read the plan, do not guess.
3. **Check the data before trusting a plan.** The first loader evaluated `random()` once for
   the whole statement, so all 100,000 meetings had the same timestamp and every window
   query returned zero rows in a fraction of a millisecond. The plans looked fine and meant
   nothing. `count(DISTINCT starts_at)` caught it.

## Reproduce

```
docker compose exec -T db psql -U agentlab -d postgres -c "CREATE DATABASE agentlab_drill"
DATABASE_URL=postgresql+psycopg://agentlab:agentlab@localhost:5433/agentlab_drill \
  uv run alembic -x url=postgresql+psycopg://agentlab:agentlab@localhost:5433/agentlab_drill upgrade head
docker compose exec -T db psql -U agentlab -d agentlab_drill -f - < docs/explain/load.sql
docker compose exec -T db psql -U agentlab -d agentlab_drill -c "EXPLAIN (ANALYZE, BUFFERS) <query>"
```
