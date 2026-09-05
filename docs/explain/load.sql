-- Synthetic drill data: 2,000 people, 100,000 meetings over 2025-2026, ~300,000 attendees.
-- Run against a scratch database migrated to head, never against dev or test data:
--   createdb agentlab_drill && DATABASE_URL=...agentlab_drill alembic upgrade head
--   psql ... -d agentlab_drill -f docs/explain/load.sql
--
-- GOTCHA, learned the hard way: random() inside an uncorrelated LATERAL subquery is evaluated
-- ONCE for the whole statement, so the first version of this loader gave all 100,000 meetings
-- the same start time and every timing measured on it was meaningless. random() in the target
-- list of the INSERT is evaluated per row.

INSERT INTO people (email, display_name, timezone, focus_hours, is_resource, is_trainee, created_at)
SELECT format('person%s@example.com', g), format('Person %s', g), 'Asia/Kolkata', '{}'::jsonb,
       false, (g % 10 = 0), now()
FROM generate_series(1, 2000) g;

INSERT INTO meetings (source, external_id, series_id, is_exception, title, starts_at, ends_at,
                      organizer_id, is_external, status, raw, ingested_at)
SELECT 'synthetic',
       format('syn-%s', g),
       CASE WHEN g % 4 = 0 THEN format('series-%s', g % 500) END,
       false,
       (ARRAY['Standup','Budget review','1:1','Planning','Retro','Customer call','Interview','Design review'])[1 + (g % 8)]
         || ' ' || g,
       ts,
       ts + (15 + (random() * 105)::int) * interval '1 minute',
       1 + (random() * 1999)::int,
       (g % 7 = 0),
       CASE WHEN g % 20 = 0 THEN 'cancelled' ELSE 'confirmed' END,
       '{}'::jsonb,
       now()
FROM generate_series(1, 100000) g,
     LATERAL (SELECT timestamptz '2025-01-01 00:00+00' + random() * interval '730 days' AS ts) s
WHERE g = g;  -- the reference to g correlates the LATERAL so random() runs per row

INSERT INTO attendees (meeting_id, person_id, response_status, is_optional, is_organizer)
SELECT DISTINCT ON (m.id, p.pid) m.id, p.pid, 'accepted', false, (p.pid = m.organizer_id)
FROM meetings m
CROSS JOIN LATERAL (VALUES (m.organizer_id), (1 + (random()*1999)::int), (1 + (random()*1999)::int)) AS p(pid)
WHERE m.source = 'synthetic';

VACUUM ANALYZE people; VACUUM ANALYZE meetings; VACUUM ANALYZE attendees;
