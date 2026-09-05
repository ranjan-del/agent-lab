-- Ten questions the policy agent will actually ask of the calendar, written as plain SQL so
-- each can be run under EXPLAIN (ANALYZE, BUFFERS) against the 100k-row drill database.
-- Parameters are inlined with representative values; :person is person1000@example.com.

-- Q1  Does anything overlap this person's focus block? (the agent's core question)
SELECT m.id, m.title, m.starts_at, m.ends_at
FROM meetings m
JOIN attendees a ON a.meeting_id = m.id
JOIN people p ON p.id = a.person_id
WHERE p.email = 'person1000@example.com'
  AND m.starts_at < timestamptz '2026-03-10 12:00+00'
  AND m.ends_at   > timestamptz '2026-03-10 09:00+00';

-- Q2  Every meeting overlapping a window, regardless of attendee (room planning, load charts)
SELECT count(*)
FROM meetings
WHERE starts_at < timestamptz '2026-03-10 12:00+00'
  AND ends_at   > timestamptz '2026-03-10 09:00+00';

-- Q3  A person's most recent meeting (per user, most recent: a window function)
SELECT p.email, m.title, m.starts_at
FROM (
  SELECT a.person_id, a.meeting_id,
         row_number() OVER (PARTITION BY a.person_id ORDER BY m.starts_at DESC) AS rn
  FROM attendees a JOIN meetings m ON m.id = a.meeting_id
) ranked
JOIN people p ON p.id = ranked.person_id
JOIN meetings m ON m.id = ranked.meeting_id
WHERE ranked.rn = 1 AND p.email = 'person1000@example.com';

-- Q4  Meetings a person organised in a date range
SELECT m.id, m.title
FROM meetings m JOIN people p ON p.id = m.organizer_id
WHERE p.email = 'person1000@example.com'
  AND m.starts_at >= timestamptz '2026-01-01+00' AND m.starts_at < timestamptz '2026-04-01+00';

-- Q5  External meetings per ISO week (the compliance report)
SELECT date_trunc('week', starts_at) AS week, count(*)
FROM meetings
WHERE is_external AND starts_at >= timestamptz '2026-01-01+00'
GROUP BY 1 ORDER BY 1;

-- Q6  Title search the way a human types it
SELECT id, title FROM meetings WHERE title ILIKE '%budget review 42%';

-- Q7  Who is in this meeting
SELECT p.email, a.response_status
FROM attendees a JOIN people p ON p.id = a.person_id
WHERE a.meeting_id = 50000;

-- Q8  Trainees with no meeting in the last 30 days (anti-join)
SELECT p.email
FROM people p
WHERE p.is_trainee
  AND NOT EXISTS (
    SELECT 1 FROM attendees a JOIN meetings m ON m.id = a.meeting_id
    WHERE a.person_id = p.id AND m.starts_at > timestamptz '2026-12-01+00'
  );

-- Q9  All occurrences of one series
SELECT id, starts_at FROM meetings WHERE series_id = 'series-44' ORDER BY starts_at;

-- Q10 Meetings still running at an instant (ends_at is the only bound)
SELECT count(*) FROM meetings
WHERE starts_at <= timestamptz '2026-06-15 10:30+00'
  AND ends_at   >  timestamptz '2026-06-15 10:30+00';

-- Q2b Q2 written against the span index added in migration 0004 (same 25 rows, 1 buffer)
SELECT count(*) FROM meetings
WHERE tstzrange(starts_at, ends_at, '[)') && tstzrange(timestamptz '2026-03-10 09:00+00', timestamptz '2026-03-10 12:00+00', '[)');

-- Q10b Q10 written against the span index (containment)
SELECT count(*) FROM meetings
WHERE tstzrange(starts_at, ends_at, '[)') @> timestamptz '2026-06-15 10:30+00';

-- Q3b Q3 rewritten: collect the person's meetings first, then sort that small set
WITH mine AS MATERIALIZED (
  SELECT a.meeting_id FROM attendees a JOIN people p ON p.id = a.person_id
  WHERE p.email = 'person1000@example.com'
)
SELECT m.title, m.starts_at FROM meetings m JOIN mine ON mine.meeting_id = m.id
ORDER BY m.starts_at DESC LIMIT 1;
