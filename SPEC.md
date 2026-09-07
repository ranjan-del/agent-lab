# SPEC — agent-lab

**Status: in progress. Sections 1, 2, 3, the first entry of 7 and two decisions in 8 written 6 Sep 2026. Sections 4, 5, 6 open. Week 2 build complete offline 8 Sep; provider adapter pending.**
Answer each question in prose, not bullets. If you cannot answer one, that is the thing to
resolve before writing the code it describes.

## 1. The problem, in one paragraph a stranger understands

This agent works for one person who runs several things at once: multiple teams, client
meetings, product demos, requirement discussions and stakeholder check-ins, often ten or more
meetings in a single day, most of them recorded. Two things go wrong today. First, the
recordings are unreliable: when someone joins early and leaves, or the meeting restarts after
the first person drops, the transcript that ends up attached to the calendar event is the
wrong one or there is none at all, and without the transcript nobody can reconstruct what was
asked or promised. Second, even when every transcript exists, a day of ten meetings produces
ten sets of notes and requirements that no one has time to go back through, so commitments
get lost. Solved looks like this: when I ask "when am I free this week", the agent answers
with slots on days that are already light, spreading meetings across the week or month instead
of stacking them, and never proposes a day that is already heavy. And it works under rules I
set in three tiers. Hard rules are never broken for any reason. Middle rules may be broken
only for something top-urgent, and only after asking me. Soft rules the agent may bend on its
own judgement. Whatever it does, it tells me which rule applied.

## 2. What the agent is allowed to do

The agent has three tools. The first reads the calendar for a date range and returns the
meetings, the load per day, and the free slots; it changes nothing. The second searches the
transcripts for a date range or a topic and returns the matching passages with the meeting
each came from; it changes nothing. The third writes the task list: it creates, edits,
reorders and closes task rows in the agent's own database, each task carrying the meeting it
came from, its urgency, its due date and what I agreed to do. Everything the agent knows
about my day, week or month comes from the first two tools, and everything it produces goes
through the third. The task list is the agent's only output that persists, and it is fully
reversible: every row can be edited or deleted, nothing leaves the database, and nothing
reaches Google Calendar, email or another person. Reading is always allowed. Writing to the
task list is allowed without asking, because it can always be undone. Any action that
reaches outside the database is not a tool in this version.

Decided with section 2: a `tasks` table joins the schema in week 2 (migration 0005) with
id, meeting_id, text, urgency (hard, middle, soft), due_at, status, agreed_by_me and
created_by_run_id. Checking that a transcript really belongs to its meeting is an ingestion
check, not a tool: the agent must never see a mislinked transcript.

## 3. What the agent must refuse

The rules come in three tiers, and the tier decides who can bend them. Hard rules are never
broken and have no override; the agent refuses and names the rule. Middle rules hold in a
normal week and may be broken only for something top-urgent, and only after I say yes to that
specific case. Soft rules are preferences the agent may set aside on its own, as long as it
says so. Every number below is a starting default and lives in the `policies` table, not in
code, so changing it is a data edit, not a release.

| Tier | Rule | Default |
|---|---|---|
| Hard | Never move, shorten, or propose moving a meeting marked important or fixed | no exceptions |
| Hard | Never place or propose anything inside the focus block | 09:00 to 11:00 IST, Mon to Fri |
| Hard | Never add an external person to an internal meeting; never let transcript content leave the database | no exceptions |
| Hard | Never mark a task done that I did not confirm | no exceptions |
| Middle | No more than N meetings in one day | 6 |
| Middle | No more than N hours of meetings in one day | 4 |
| Middle | No two meetings without a gap between them | 15 minutes |
| Middle | Nothing outside working hours or on weekends | 10:00 to 19:00 IST, Mon to Fri |
| Middle | No proposing a change to a meeting starting within 24 hours | 24 hours |
| Soft | Prefer the lightest days when suggesting slots; spread new meetings rather than stack them | always try |
| Soft | Keep one-to-ones on the same day when possible | preference |
| Soft | Prefer 30-minute slots over 60 | preference |
| Soft | Prefer mornings after the focus block for client calls | 11:00 to 13:00 IST |

## 4. Where the data comes from

<!-- Decided: your own personal calendar and your own transcripts. NOT ISPF data:
     this repo is public, and organisation meeting content must never enter it. -->

## 5. The schema, and why each table exists

<!-- Nine tables. For each: what one row means, and which later week reads it. -->

## 6. What "correct" means

<!-- How will you know the agent got it right? This paragraph becomes the eval spec in W4:
     task success, policy correctness, explanation quality. -->

## 7. What this deliberately does not do

It does not send anything. No invites, no emails, no messages, no notifications, and it does
not contact anyone on my behalf. Those are the most useful things it could eventually do after
a meeting, and they are exactly the actions that cannot be undone once done, so they wait
until the policy engine exists, has been tested, and can require my approval per action.
Until then the agent produces the list of things to send and I send them.

<!-- Two more things you are choosing not to build go here. -->

## 8. The five design decisions you expect to defend

1. **The loop is hand-rolled, not a framework.** Forty lines: call the model, run the tools
   it asked for, feed results back, repeat. Two weeks of owning this before adopting anything,
   so that when a framework is considered the question "what does it buy" has an answer.
2. **Tool failures fail open to the model.** An unknown tool name, arguments that fail the
   schema, or a tool that raises all become a result the model reads, never an exception that
   ends the run. The model is the party that can repair its own mistake. The rejected
   alternative, failing closed, turns one hallucinated argument into a dead run. The step
   limit is what bounds a permanently broken tool, and it is not optional.

<!-- Three more decisions go here as they are made. -->
