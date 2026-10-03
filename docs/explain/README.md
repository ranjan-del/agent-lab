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
| Q6 | **Deferred, decided 30 Sep** (below): a GIN trigram index works, and nothing asks the question | 51 ms | 0.6 ms with the index, measured on a scratch copy and not shipped | `before/q6.txt`, `after/q6-trgm-gin-not-shipped.txt` |

## Q6 and pg_trgm: deferred, with a trigger (decided 30 Sep)

Measured on a scratch database (`agentlab_trgm_scratch`, dropped afterwards) loaded with
`load.sql` at the drill's size, 100,000 meetings, median of seven runs each.

| Measure | Without the index | With `gin (title gin_trgm_ops)` |
|---|---|---|
| Q6, `ILIKE '%budget review 42%'`, 139 rows | 50.6 ms, seq scan, 1,661 buffers | 0.6 ms, bitmap index scan, 90 buffers |
| `ILIKE '%budget%'`, 12,500 rows | 45.2 ms | 9.1 ms, still reads every heap page |
| `ILIKE '%42%'`, a two-character pattern | 44.0 ms | 42.1 ms, seq scan: a trigram needs three characters |
| Index size, build time | | 3.3 MB on a 13 MB table, 0.26 s |
| 10,000 meeting inserts | 165 ms | 269 ms, 63% slower |
| Q6 at 5,000 meetings, no index | 2.5 ms | |

The index does what it promises: about 80 times faster on a selective pattern. I am not adding it,
because nothing issues the query. `read_calendar_window` selects by time span through the GiST
index, `search_transcripts` is vector search over chunks, and `write_tasks` writes by id; no tool,
endpoint or prompt filters meetings by title. And 100,000 meetings is twenty times my real
calendar: ten meetings a working day for two years is about 5,000 rows, where the unindexed scan
is 2.5 ms. An index nothing reads is a 63% tax on every ingest, paid for a question nobody asks.

Rejected alternative: adopt it now, in the migration after 0007, so the drill closes with every
slow query fixed. That buys a number on this page and costs the ingest path, the extension, and
a query shape (`ILIKE` over `title`) the code would then have to preserve. The other rejected
alternative, full-text search (`to_tsvector` on `title`), does not answer Q6 at all: it matches
words, not substrings, so "budget review 42" would not find "Budget review 4217".

**Trigger to revisit:** a tool or endpoint that filters meetings by a substring of the title
(for example "find my meeting called X") lands, **and** that query measures over 10 ms at the
real row count. Then the follow-up is one migration with exactly this DDL, and nothing else:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_meetings_title_trgm ON meetings USING gin (title gin_trgm_ops);
-- downgrade: DROP INDEX ix_meetings_title_trgm; DROP EXTENSION pg_trgm;
```

Hybrid retrieval in W8 does not trip this trigger: its lexical half is ranked full-text search
over transcript chunks, a different column and a different operator.

One thing the rerun found, unrelated to Q6: on this load `count(DISTINCT starts_at)` on
`meetings` is 1 again, so the planner has folded the `WHERE g = g` correlation away and gotcha
3 below is back. Titles do not depend on it, so the Q6 numbers stand; the window queries' numbers do
not, and the loader needs a real per-row `random()` before they are rerun.

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
