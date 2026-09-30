# Tool calling, structured output, and repairing a bad call instead of crashing on it

Week 3 fundamentals. Every claim about this repository below is either cited to a `file:line`
or measured by `docs/notes/scripts/2026-09-30-tool-calling.py`, which drives the real toolkit,
loop and policy engine. It needs no database, no network and no API key: the database is
replaced by two stand-ins (one that returns no rows, one that is down), and the model is the
repo's own `ScriptedModel` or a model that never stops asking. Re-run it with
`uv run python docs/notes/scripts/2026-09-30-tool-calling.py`. Its full output is the appendix.

Every claim about how models use tools is read from Anthropic's own pages on 2026-09-30,
listed at the end. Primary sources only.

The question this note has to answer is SPEC section 8, decision 2: tool failures fail open to
the model, and the step limit is what bounds a permanently broken tool. Section 5 is the
argument; everything before it is what the argument stands on.

## 1. What a tool is, to the model

The model never sees a Python function. It sees three things, and the API turns them into
system prompt text ("Here are the functions available in JSONSchema format", in the
define-tools page):

| Part | What the docs require | This repo |
|---|---|---|
| `name` | `^[a-zA-Z0-9_-]{1,128}$` | `read_calendar_window`, `search_transcripts`, `write_tasks` (`toolkit.py:41,51,61`) |
| `description` | "by far the most important factor in tool performance"; at least 3 to 4 sentences | 37, 37 and 44 words, three to four sentences each (`toolkit.py:42-66`) |
| `input_schema` | a JSON Schema object | the pydantic model's schema, `args_model` on `Tool` (`tools.py:16`) |
| `input_examples` | optional, schema-validated, 20 to 200 tokens each | none |

So a description is not documentation. It is prompt text the model reads on every turn, and
it is the only place to say things the schema cannot: when to use the tool, when not to, and
what it will not return. `write_tasks` does this well: "Never set status to done; only I
confirm completion. Use dropped for a task that no longer applies" (`toolkit.py:63-66`)
tells the model the rule and the legal alternative in the same breath. The engineering post
puts it as describing the tool "to a new hire on your team".

What the model is actually shown for each tool, measured:

| Tool | Description words | Properties | Required | `additionalProperties` | Strict mode cannot enforce | Python-only validators |
|---|---|---|---|---|---|---|
| `read_calendar_window` | 37 | 3 | start, end | absent | none | `_aware`, `_ordered` |
| `search_transcripts` | 37 | 5 | query | absent | maximum, minLength, minimum | `_aware` |
| `write_tasks` | 44 | 2 | none | absent | minLength | none |

The last column is the one that matters later. "Must be timezone-aware" and "end must be
after start" (`tools_calendar.py:32-43`) are Python validators. They run, but they are not in
the schema, so the model only learns about them from the description or from an error. The
calendar description does not mention either. The first time a model sends a naive
datetime, the error message in section 3 is its only teacher.

One gap, recorded rather than fixed: nothing in the repo emits these schemas yet.
`ModelClient.complete` takes `tools: list[Any]` (`types.py:54`) and `Tool` has no method that
produces `{name, description, input_schema}` (`tools.py:12-21`). The provider adapter is still
deferred, so the table above is what the model *would* be shown.

## 2. Designing tools for an agent, not for a programmer

The engineering post's central claim is that tools are "a contract between deterministic
systems and non-deterministic agents", and a contract written for a programmer is the wrong
one. Its advice, next to what this repo does:

| Principle (engineering post, define-tools page) | This repo | Verdict |
|---|---|---|
| Few high-leverage tools, not an API mirror. `schedule_event` over `list_users` + `list_events` + `create_event` | three tools for the three jobs SPEC section 2 names | holds |
| Consolidate: return what the next step needs | `read_calendar_window` returns meetings, load per day *and* free slots (`tools_calendar.py:79-87`), so "when am I free" is one call, not a calendar dump the model has to do arithmetic on | holds, and it is the best-designed of the three |
| Namespacing (`asana_search`, `jira_search`) when tools span services | one service, three tools | not needed yet; needed the day a second calendar source arrives |
| Return meaningful context: names over opaque ids | each passage carries its meeting's id, title and start (`tools_transcripts.py:65-77`) | holds; the id is kept because `write_tasks` needs it |
| Token efficiency: filtering, pagination, sensible defaults (Claude Code caps a tool response at 25,000 tokens) | `limit` defaults to 5, capped at 20 (`tools_transcripts.py:32`) | holds for search; the calendar window is unbounded, so a year-long window returns every meeting |
| Unambiguous parameter names (`user_id`, not `user`) | `meeting_id`, `due_at`, `id` inside `update` | mostly; `id` in `TaskUpdate` (`tools_tasks.py:33`) has no description |
| Actionable errors, not "opaque error codes or tracebacks" | section 3 | mostly holds, with two warts |

The design point I underrated: **the response is part of the tool's interface to the model,
exactly as the arguments are.** `free_slots` exists so the model never has to compute gaps
between meetings itself, which is arithmetic it can get wrong and nobody would notice.

## 3. Structured output: what a schema buys, and where it stops

Two separate API features constrain the model's output, and both use grammar-constrained
sampling (the strict-tool-use and structured-outputs pages):

| Feature | Where | Guarantees |
|---|---|---|
| Strict tool use | `"strict": true` on a tool definition | tool `input` follows `input_schema`; tool `name` is always valid |
| JSON outputs | `output_config.format` with a `json_schema` | the text response parses and matches the schema |

Strict mode is not supported on everything, and what it does not cover is exactly this
repo's most important checks:

| Where strict mode still fails | Source | Consequence here |
|---|---|---|
| `minimum`, `maximum`, `minLength`, `maxLength` are unsupported; SDK helpers move them into the description and validate client-side | structured-outputs page | `limit <= 20` and non-empty `query` stay pydantic's job (table in section 1) |
| `additionalProperties` must be `false` | same | all three argument schemas omit it today (section 1), so strict mode would reject them as written |
| Semantics are not types | by construction | a well-typed ISO string with no offset, or an end before its start, passes any schema; only `_aware` and `_ordered` catch it |
| `stop_reason: "refusal"` or `"max_tokens"`: output "may not match your schema" | structured-outputs page | the adapter must check `stop_reason` before trusting a parsed call |
| Enum casing is not guaranteed ("typically in the first letter of a word following a space") | same | `urgency` values are one lowercase word, so low risk, but a `"Hard"` would fail pydantic's `Literal` |
| Forced `tool_choice` (`any`, `tool`) returns a 400 on Opus 5.5 and Sonnet 5.5 | define-tools page | use `auto` plus strict, as the docs say |

**The rule this justifies:** strict mode moves type errors from runtime to sampling, which is
worth having, but it does not remove server-side validation, and so it does not remove the
need to decide what happens when validation fails. That decision is section 5.

What the repo does with a bad call today, measured through `loop._invoke` (`loop.py:82-97`),
the function the loop calls for every tool call (`loop.py:55`):

| Case | Raised? | Error? | What the model reads (trimmed) |
|---|---|---|---|
| Unknown tool name (`read_calendar`) | no | yes | `unknown tool 'read_calendar'; available: ['read_calendar_window', 'search_transcripts', 'write_tasks']` |
| Missing required arg | no | yes | `invalid arguments for 'read_calendar_window': start: Input should be a valid datetime...` |
| Naive datetime | no | yes | `start: Value error, must be timezone-aware, e.g. 2026-09-08T10:00:00+05:30; end: ...` |
| End before start | no | yes | `invalid arguments for 'read_calendar_window': : Value error, end must be after start` |
| Wrong type (`limit: "five"`) | no | yes | `limit: Input should be a valid integer...` |
| Out of range (`limit: 50`) | no | yes | `limit: Input should be less than or equal to 20` |
| Enum miss (`urgency: "urgent"`) | no | yes | `create.0.urgency: Input should be 'hard', 'middle' or 'soft'` |
| Tool-level refusal (`status: done`) | no | yes | `hard rule: never mark a task done that I did not confirm. Leave status alone, or set it to 'dropped'...` |
| Tool raises (database down) | no | yes | `'search_transcripts' failed: ConnectionError: connection to server at localhost:5432 refused` |
| **Misspelt key (`creates`)** | no | **no** | `{"created": [], "updated": []}` |
| Valid call | no | no | the window, meetings, load and free slots |

Eleven cases, nothing raised. The nine that report an error are good repair material: each names the tool, the field
and what a legal value looks like, which is what the handle-tool-calls page asks for
("include what went wrong and what Claude should try next"). The unknown-tool case is the
best of them, because it lists the real names, so a near miss is a one-step fix.

Two warts. The end-before-start error has an empty location, `: Value error`, because a
model-level validator has `loc == ()` and `loop.py:94` joins it anyway. Readable, but it
should say which fields. The second wart is the misspelt key, and it is not a wart. It is
section 4.

## 4. The failure fail-open cannot see

`WriteTasksArgs` has two fields, both defaulting to empty lists (`tools_tasks.py:40-42`).
Pydantic's default is `extra="ignore"`. So `{"creates": [...]}` validates as "create nothing,
update nothing", `write_tasks` does exactly that, and returns success.

Through a full run with the scripted model:

| Field | Value |
|---|---|
| Outcome | `completed` |
| Tool output | `{'created': [], 'updated': []}` |
| Model's answer | "Saved: send deck, urgency hard." |
| Rows written | 0 |

This is silent partial success, the worst failure an agent has: the run is green, the answer
is confident, and the task is lost. Fail-open depends on the mistake producing an error, and
here nothing does. All three argument models behave the same way:

| Tool | Valid call plus one unknown key |
|---|---|
| `read_calendar_window` | accepted, `bogus` silently dropped |
| `search_transcripts` | accepted, `bogus` silently dropped |
| `write_tasks` | accepted, `bogus` silently dropped |

The repo already knows the fix, because the policy engine applies it to its own data:
"Every shape forbids unknown keys. A misspelt key must fail, not vanish" (`rules.py:31-34`).
The tool arguments, which come from the least trustworthy source in the system, do not get
the same protection. Applied on a subclass in the script, `src/` untouched:

| | Today (`extra="ignore"`) | Proposed (`extra="forbid"`) |
|---|---|---|
| `{"creates": [...]}` | validates to `create=[]`, `update=[]` | `invalid arguments for 'write_tasks': creates: Extra inputs are not permitted` |
| Schema `additionalProperties` | absent | `False` |

One setting turns a silent success into an ordinary repairable error, and makes the schema
strict-mode ready as a side effect.

## 5. Fail open or fail closed: the justification for SPEC decision 2

Same script, same bad first call (naive datetimes), then a corrected call, then an answer.
Once through the real loop, once through a fail-closed copy of it written in the script for
comparison (the same loop with no `except`):

| Strategy | Outcome | Model calls | Steps | Answer |
|---|---|---|---|---|
| Fail open (`loop.run`) | completed | 3 | 5 | "Thursday 1 October is clear from 10:00 to 19:00." |
| Fail closed | dead at model call 1: `ValidationError` | 1 | none | none |

The model read this before its second call, and it was enough to repair the call:

```
{"error": "invalid arguments for 'read_calendar_window': start: Value error, must be timezone-aware, e.g. 2026-09-08T10:00:00+05:30; end: Value error, must be timezone-aware, e.g. 2026-09-08T10:00:00+05:30"}
```

The argument, then:

| Question | Fail open | Fail closed |
|---|---|---|
| Who made the mistake? | the model | the model |
| Who can fix it? | the model, now, with the error in context | a human, later, by re-running the whole task |
| Cost of one hallucinated argument | one extra model turn | the whole run |
| What the docs say the model does | "Claude will retry 2-3 times with corrections before apologizing to the user" (handle-tool-calls page) | nothing; it never sees the error |
| Worst case | the model never repairs it, and loops | none, because it stops at once |
| What bounds the worst case | the step limit | nothing is needed |

Fail closed has one real advantage, the last two rows: it cannot loop. So fail open is only
safe with a bound, and the bound is `max_steps`: the loop runs `range(max_steps)`
(`loop.py:33`), defaults to 8 (`execute.py:26`, `cli.py:28`), and ends with
`outcome="step_limit"` and `answer=None` (`loop.py:77-79`), not with the last text dressed up
as an answer. A model that asks for a tool whose database is down, forever:

| `max_steps` | Outcome | Model calls | Tool errors | Steps |
|---|---|---|---|---|
| 1 | step_limit | 1 | 1 | 2 |
| 3 | step_limit | 3 | 3 | 6 |
| 8 | step_limit | 8 | 8 | 16 |
| 20 | step_limit | 20 | 20 | 40 |

Exactly linear, and exactly bounded: the limit is the number of model calls a broken tool can
cost, and nothing else. At 8, with the docs' 2 to 3 retries per mistake, the limit leaves room
for a real task with one or two repairs in it and stops a dead one after 8 model calls. That
is why the SPEC says the limit "is not optional": without it, decision 2 is an infinite loop
with good error messages.

Where the rule stops applying. Fail open is right for **the model's** mistakes. It is wrong for
the harness's own, and the repo already draws that line in two places:

| Raised, on purpose | Where | Why it must not go to the model |
|---|---|---|
| `IncompleteContext` | `policy/types.py:142-152` | the caller forgot to load a meeting; the model cannot load it |
| `UnknownRule` | `policy/engine.py:72-73` | a policy row with no test; "a rule nobody can evaluate must not pass as harmless" |

`loop.py:96` catches every `Exception`, so a programming bug inside a tool (an `AttributeError`,
say) also goes to the model as `'x' failed: AttributeError: ...`. The model cannot fix that
either. The step limit still bounds it, so it is not dangerous, but the trace cannot tell "the
model sent a bad argument" from "our code is broken". Worth separating when the recorder
starts counting failures.

## 6. The policy gate: a refusal is a result too

The policy engine is a pure function from (action, context, rows) to a `Decision`
(`policy/engine.py:53-99`): no model, no session, no I/O. It is not wired into the loop yet;
no module outside `policy/` imports it (that wiring is Phase 5, in progress elsewhere). So the
rendering below is the script's own, to show the shape: a refusal is the same kind of thing as
a validation error, a result the model reads and acts on, not an exception.

| Proposed action | Outcome | Rule | What the model would read (trimmed) |
|---|---|---|---|
| Slot 09:30 to 10:00 Thu | refuse | `focus_block` | `refuse by focus_block (hard): Never place or propose anything inside the focus block.`, also `working_hours` |
| Mark task 7 done | refuse | `never_mark_done` | `refuse by never_mark_done (hard): Never mark a task done that I did not confirm.` |
| Slot 18:30 to 19:30 Thu | ask_override | `working_hours` | `ask_override by working_hours (middle): ...`, plus what would have to be true |
| Slot 14:00 to 15:00 Thu | allow | none | `Preference set aside (prefer_short_slots): ...` |
| Move meeting 99 (not loaded) | **raises** `IncompleteContext` | none | nothing: this is a caller bug, and it fails closed |

That table is section 5 again. A hard refusal is fail open in exactly the sense decision 2
means: the run continues, the model reads which rule stopped it, and it can do the permitted
thing instead, which is what the system prompt asks of it ("If a rule stops you, say which
rule", `prompt.py:39`). What a refusal must never be is an exception that ends the run,
because then the rule's name reaches a log and not the person who asked.

Two things to carry into the wiring. First, a refusal that the model *keeps* retrying is the
permanently broken tool of section 5, and the same step limit bounds it; a refusal is
deterministic (`engine.py:8-9`), so a retry of the same action can never succeed. Second, the
"never done" rule is already enforced inside `write_tasks` (`tools_tasks.py:59-60`) and again
in the engine (`engine.py:256-263`), and they disagree: the engine allows a task I confirmed
(`confirmed_done_task_ids`), the tool refuses every `done`. One of the two has to win when
the gate is wired, or the model will read two different answers to the same question.

## 7. What this repo does today, and the one change it should make

| Property | Today | Evidence |
|---|---|---|
| Unknown tool, bad arguments, tool raising: all become results | yes, 9 error cases out of 11 calls, none raised | section 3; `loop.py:82-97` |
| Errors are actionable | yes, with one empty location | section 3; `loop.py:94` |
| Bounded by a step limit, reported as `step_limit` | yes | section 5; `loop.py:33,77-79` |
| Unknown argument keys fail | **no**, silently dropped on all three tools | section 4 |
| Schemas strict-mode ready | no, `additionalProperties` absent | section 1 |
| Errors flagged as errors to the provider | not yet: the error is only a key in JSON content of a `role: tool` message (`loop.py:68-75`); the Anthropic API wants a `tool_result` with `is_error: true` | handle-tool-calls page |

**The one improvement: forbid unknown keys on every tool argument model.** Add
`model_config = ConfigDict(extra="forbid")` to `NewTask`, `TaskUpdate` and `WriteTasksArgs`
(`tools_tasks.py:21,30,40`), `CalendarWindowArgs` (`tools_calendar.py:25`) and
`SearchTranscriptsArgs` (`tools_transcripts.py:24`), exactly as `_Shape` already does for
policy rows (`rules.py:31-34`). Test-first: a scripted run that sends `{"creates": [...]}`
must get an error naming `creates`, and must write no row.

Why this one and not the others. It is the only finding where fail open *does not happen*:
every other gap produces a readable error the model can repair, and this one produces a
confident success with nothing written. It costs one line per model. And it is also the first
step to strict mode, which requires `additionalProperties: false`.

## What I would tell someone starting this

| Claim | The evidence behind it |
|---|---|
| The description is prompt text, and the most important part of the tool | define-tools: "by far the most important factor in tool performance" |
| Design the response for the model's next step, not for completeness | `free_slots` saves the model arithmetic it could get wrong (`tools_calendar.py:86`) |
| Strict mode fixes types, not meaning | 3 validators and 4 constraints here that no schema can enforce |
| Fail open on the model's mistakes | one bad argument: completed in 3 calls open, dead in 1 closed |
| Fail closed on your own | `IncompleteContext`, `UnknownRule` raise by design |
| Fail open is only safe with a bound | a dead tool costs exactly `max_steps` model calls: 1, 3, 8, 20 |
| A refusal is a result, not an exception | the engine returns a `Decision`; the model says which rule |
| The dangerous failure is the one that raises no error | `creates` for `create`: completed, "Saved", 0 rows |

The thing I got wrong going in: I thought the risk in fail open was the loop, a model burning
steps on a tool that will never work, and I came to defend the step limit. The step limit is
fine; section 5 shows it is exact. The real risk is the opposite case, where the tool never
fails at all. Fail open is a promise that every mistake becomes an error the model can read,
and that promise is only as good as the validation behind it. `extra="ignore"` breaks it
quietly, on the one tool that writes.

## Sources

| Source | URL | Used for |
|---|---|---|
| Anthropic engineering, "Writing effective tools for agents" (Ken Aizawa, 11 Sep 2025) | https://www.anthropic.com/engineering/writing-tools-for-agents | sections 1, 2: the contract, consolidation, namespacing, meaningful context, 25,000-token cap, actionable errors |
| Claude docs, Define tools | https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools | section 1: definition fields, name regex, description guidance, `input_examples`; `tool_choice` limits |
| Claude docs, Handle tool calls | https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls | sections 3, 5, 7: `is_error`, instructive errors, "retry 2-3 times" |
| Claude docs, Strict tool use | https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use | section 3: grammar-constrained sampling, guarantees |
| Claude docs, Structured outputs | https://platform.claude.com/docs/en/build-with-claude/structured-outputs | section 3: unsupported schema features, refusal and `max_tokens`, enum casing |

## Appendix: the script's output

Verbatim, from `uv run python docs/notes/scripts/2026-09-30-tool-calling.py` on 2026-09-30.

```
==============================================================================
1. What the model is shown for each tool: name, description, input schema
==============================================================================
tool                   desc words props required           addlProps strict cannot enforce    python-only validators
read_calendar_window           37     3 start,end             absent -                        _aware,_ordered
search_transcripts             37     5 query                 absent maximum,minLength,minimum _aware
write_tasks                    44     2 (none)                absent minLength                -

search_transcripts input_schema, exactly as pydantic emits it:
{
  "properties": {
    "query": {
      "description": "What to look for: a topic, a promise, a name.",
      "minLength": 1,
      "title": "Query",
      "type": "string"
    },
    "start": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Only transcripts captured at or after this instant.",
      "title": "Start"
    },
    "end": {
      "anyOf": [
        {
          "format": "date-time",
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Only transcripts captured before this instant.",
      "title": "End"
    },
    "limit": {
      "default": 5,
      "maximum": 20,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "timezone": {
      "default": "Asia/Kolkata",
      "title": "Timezone",
      "type": "string"
    }
  },
  "required": [
    "query"
  ],
  "title": "SearchTranscriptsArgs",
  "type": "object"
}

==============================================================================
2. Malformed calls through loop._invoke: raised, or a result the model can read?
==============================================================================
case                   raised error  what the model reads
unknown tool name      no     yes    {"error": "unknown tool 'read_calendar'; available: ['read_calendar_window', 'search_transcri...
missing required arg   no     yes    {"error": "invalid arguments for 'read_calendar_window': start: Input should be a valid datet...
naive datetime         no     yes    {"error": "invalid arguments for 'read_calendar_window': start: Value error, must be timezone...
end before start       no     yes    {"error": "invalid arguments for 'read_calendar_window': : Value error, end must be after sta...
wrong type             no     yes    {"error": "invalid arguments for 'search_transcripts': limit: Input should be a valid integer...
out of range           no     yes    {"error": "invalid arguments for 'search_transcripts': limit: Input should be less than or eq...
enum miss              no     yes    {"error": "invalid arguments for 'write_tasks': create.0.urgency: Input should be 'hard', 'mi...
tool-level refusal     no     yes    {"error": "hard rule: never mark a task done that I did not confirm. Leave status alone, or s...
tool raises            no     yes    {"error": "'search_transcripts' failed: ConnectionError: connection to server at localhost:54...
misspelt key           no     no     {"created": [], "updated": []}
valid call             no     no     {"window": {"start": "2026-10-01T10:00:00+05:30", "end": "2026-10-02T10:00:00+05:30"}, "meeti...

Full text of three of them, as the model would read it:
  unknown tool name: {"error": "unknown tool 'read_calendar'; available: ['read_calendar_window', 'search_transcripts', 'write_tasks']"}
  naive datetime: {"error": "invalid arguments for 'read_calendar_window': start: Value error, must be timezone-aware, e.g. 2026-09-08T10:00:00+05:30; end: Value error, must be timezone-aware, e.g. 2026-09-08T10:00:00+05:30"}
  tool raises: {"error": "'search_transcripts' failed: ConnectionError: connection to server at localhost:5432 refused"}

==============================================================================
3. The misspelt key: extra='ignore' today vs extra='forbid' proposed
==============================================================================
today    : validates, create=[], update=[]  (the key vanished)
proposed : invalid arguments for 'write_tasks': creates: Extra inputs are not permitted
schema additionalProperties, today vs proposed: absent vs False

Every tool's argument model, sent one unknown key alongside a valid call:
  read_calendar_window   accepted, 'bogus' silently dropped
  search_transcripts     accepted, 'bogus' silently dropped
  write_tasks            accepted, 'bogus' silently dropped

==============================================================================
4. One bad call, then a corrected one: fail open vs fail closed
==============================================================================
strategy     outcome                                model calls steps  answer
fail open    completed                                        3     5  Thursday 1 October is clear from 10:00 to 19:00.
fail closed  dead at model call 1: ValidationError            1     -  None

What the fail-open model read before its second call:
  {"error": "invalid arguments for 'read_calendar_window': start: Value error, must be timezone-aware, e.g. 2026-09-08T10:00:00+05:30; end: Value error, must be timezone-aware, e.g. 2026-09-08T10:00:00+05:30"}

==============================================================================
5. The same fail-open loop, when the mistake never surfaces
==============================================================================
outcome : completed
tool out: {'created': [], 'updated': []}
answer  : Saved: send deck, urgency hard.
rows written: 0 (create=[] reached write_tasks, which wrote nothing and said so)

==============================================================================
6. A permanently broken tool, bounded only by max_steps
==============================================================================
max_steps outcome      model calls tool errors steps
        1 step_limit             1           1     2
        3 step_limit             3           3     6
        8 step_limit             8           8    16
       20 step_limit            20          20    40
answer on step_limit: None

==============================================================================
7. The policy gate: a refusal is a Decision the model can read, not an exception
==============================================================================
proposed action        outcome       rule               what the model would read
slot 09:30-10:00 Thu   refuse        focus_block        {"error": "refuse by focus_block (hard): Never place or propose anything insi...
mark task 7 done       refuse        never_mark_done    {"error": "refuse by never_mark_done (hard): Never mark a task done that I di...
slot 18:30-19:30 Thu   ask_override  working_hours      {"error": "ask_override by working_hours (middle): Nothing outside working ho...
slot 14:00-15:00 Thu   allow         -                  {"allowed": true, "notes": ["Preference set aside (prefer_short_slots): Prefe...
move meeting 99        RAISES        -                  IncompleteContext: meeting 99 is not in the context; the caller must load it b...

The first refusal in full, as the model would read it:
{
  "error": "refuse by focus_block (hard): Never place or propose anything inside the focus block.",
  "detail": "Thu 2026-10-01 09:30 to 10:00 Asia/Kolkata overlaps the block 09:00 to 11:00 Asia/Kolkata, Mon, Tue, Wed, Thu, Fri on Thu 2026-10-01",
  "also": [
    "working_hours"
  ],
  "needs": null
}
```
