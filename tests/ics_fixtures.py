"""Hand-written .ics fixtures.

Written by hand rather than generated, because the point of each one is a specific awkward
case, and a generator would smooth exactly those away.
"""

SIMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:evt-simple
DTSTART:20260901T043000Z
DTEND:20260901T051500Z
SUMMARY:Design review
ORGANIZER:mailto:ranjan@example.com
ATTENDEE;CN=Ranjan;PARTSTAT=ACCEPTED:mailto:ranjan@example.com
ATTENDEE;CN=Sriram;PARTSTAT=NEEDS-ACTION:mailto:sriram@example.com
ATTENDEE;CN=Boardroom;CUTYPE=RESOURCE:mailto:room-1@resource.example.com
END:VEVENT
END:VCALENDAR
"""

EXTERNAL = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:evt-external
DTSTART:20260902T090000Z
DTEND:20260902T093000Z
SUMMARY:Client call
ORGANIZER:mailto:ranjan@example.com
ATTENDEE:mailto:ranjan@example.com
ATTENDEE:mailto:buyer@bigcorp.com
END:VEVENT
END:VCALENDAR
"""

# A weekly standup at 10:00 New York time, spanning the 1 November 2026 DST change.
# Before the change 10:00 local is 14:00 UTC; after it, 15:00 UTC. An expansion that adds
# a fixed seven days in UTC gets every occurrence after the change wrong by an hour.
DST_WEEKLY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
DTSTART:20260308T020000
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
DTSTART:20261101T020000
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:evt-standup
DTSTART;TZID=America/New_York:20261020T100000
DTEND;TZID=America/New_York:20261020T103000
RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=6
SUMMARY:Standup
ORGANIZER:mailto:ranjan@example.com
ATTENDEE:mailto:ranjan@example.com
END:VEVENT
END:VCALENDAR
"""

# A weekly series where one occurrence was cancelled (EXDATE) and another was moved
# (a second VEVENT carrying RECURRENCE-ID).
SERIES_WITH_EXCEPTIONS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:evt-series
DTSTART:20260907T043000Z
DTEND:20260907T050000Z
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=4
EXDATE:20260914T043000Z
SUMMARY:Weekly sync
ORGANIZER:mailto:ranjan@example.com
ATTENDEE:mailto:ranjan@example.com
END:VEVENT
BEGIN:VEVENT
UID:evt-series
RECURRENCE-ID:20260921T043000Z
DTSTART:20260921T113000Z
DTEND:20260921T120000Z
SUMMARY:Weekly sync (moved to afternoon)
ORGANIZER:mailto:ranjan@example.com
ATTENDEE:mailto:ranjan@example.com
END:VEVENT
END:VCALENDAR
"""

# All-day events carry a date with no time, so they cannot answer "does this overlap a
# focus block". They are skipped rather than given an invented midnight.
ALL_DAY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:evt-holiday
DTSTART;VALUE=DATE:20260902
DTEND;VALUE=DATE:20260903
SUMMARY:Public holiday
END:VEVENT
END:VCALENDAR
"""
