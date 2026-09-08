"""Week 2 fundamentals measurements: embeddings, similarity, chunking.

Every number in docs/notes/2026-09-08-embeddings-similarity-chunking.md comes out of this
file. Nothing here touches Postgres or the network past the model download, so it runs
anywhere the embed group is installed:

    uv run --group embed python docs/notes/scripts/2026-09-08-embeddings.py

The transcript is synthetic on purpose. This repo is public and the SPEC forbids
organisational content, so it is written to have the SHAPE of a real standup: interruptions,
a correction, pronouns whose referent is a turn away, and one decision that reverses another.
"""

from __future__ import annotations

import itertools

from agent_lab.embeddings.local import LocalEmbedder
from agent_lab.ingest.chunking import Turn, chunk_turns, parse_turns

E = LocalEmbedder()


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def sim(t1: str, t2: str) -> float:
    v = E.embed([t1, t2])
    return cos(v[0], v[1])


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# A synthetic but structurally realistic standup. No real organisational content:
# the repo is public and the SPEC forbids it.
TRANSCRIPT = """
Priya (09:01): Morning. Let us keep this to fifteen minutes. Ravi, start us off.
Ravi: I finished the retry logic on the payment webhook yesterday. It now backs off
exponentially and gives up after five attempts instead of hammering the endpoint forever.
Ravi: The one thing I could not close is the idempotency key. Two duplicate charges got
through on Tuesday because we keyed on the request id and Stripe reuses those across retries.
Priya: How bad is Tuesday?
Ravi: Two customers, both refunded already. Support has the list.
Priya: Fine. Make the idempotency key the fix for today, not the retry polish.
Sriram: Quick one from me. The nightly export has been silently truncating at ten thousand
rows since the migration. Nobody noticed because the job still exits zero.
Priya: Since when?
Sriram: Since the twelfth. Six nights of partial data in the warehouse.
Priya: That is worse than the duplicate charges. Sriram, drop the dashboard work and backfill
those six nights first. I want the exit code fixed too, a job that loses data should fail loudly.
Sriram: I can backfill today. The exit code change touches the shared runner, so that
needs a review.
Anita: I have the accessibility audit results. Fourteen issues, four of them blockers, all four
are missing form labels on the checkout page.
Priya: Ship the four blockers this week. The other ten go in the backlog with a date.
Anita: There is one more thing. We should ship the checkout rewrite on Friday.
Priya: No. We ship the checkout rewrite the Friday after the audit fixes land, not this one.
Anita: Understood. The Friday after.
Ravi: Last thing, the staging database is out of disk again. Third time this month.
Priya: Raise it with infra, and put the alert threshold at seventy percent instead of ninety.
""".strip()


# ---------------------------------------------------------------- 1. model facts
rule("1. Model facts")
print(f"name          : {E.name}")
print(f"dimension     : {E.dimension}")
print(f"max_tokens    : {E.max_tokens}")
probe = E.embed(["a short sentence about shipping"])[0]
print(f"norm of vector: {sum(v * v for v in probe) ** 0.5:.6f}  (normalize_embeddings=True)")
print(f"self-similarity of identical text: {sim('ship on Friday', 'ship on Friday'):.6f}")


# ------------------------------------------------------- 2. tokens vs words vs chars
rule("2. What the tokenizer does to transcript text")
turns = parse_turns(TRANSCRIPT)
body = "\n".join(t.rendered() for t in turns)
n_tok = E.count_tokens(body)
n_word = len(body.split())
n_char = len(body)
print(f"turns parsed  : {len(turns)}")
print(f"characters    : {n_char}")
print(f"words         : {n_word}")
print(f"tokens        : {n_tok}")
print(f"tokens/word   : {n_tok / n_word:.3f}")
print(f"chars/token   : {n_char / n_tok:.3f}")

for label, text in [
    ("plain prose", "We decided to ship the checkout rewrite the Friday after the audit lands."),
    (
        "with speaker",
        "Priya: We decided to ship the checkout rewrite the Friday after the audit lands.",
    ),
    ("names+numbers", "Sriram: the nightly export truncated at 10000 rows since the 12th."),
]:
    w, t = len(text.split()), E.count_tokens(text)
    print(f"  {label:14s} words={w:3d} tokens={t:3d} ratio={t / w:.2f}")


# ------------------------------------------------------ 3. the similarity floor
rule("3. Cosine has no absolute scale: the floor is not zero")
unrelated = [
    "The nightly export truncated at ten thousand rows.",
    "Make the idempotency key the fix for today.",
    "Fourteen accessibility issues, four of them blockers.",
    "The staging database is out of disk again.",
    "Put the alert threshold at seventy percent.",
    "A recipe for lemon drizzle cake with poppy seeds.",
    "The train from Bangalore to Mysore takes three hours.",
    "Photosynthesis converts light energy into chemical energy.",
]
vecs = E.embed(unrelated)
pairs = [cos(vecs[i], vecs[j]) for i, j in itertools.combinations(range(len(unrelated)), 2)]
pairs.sort()
print(f"pairs compared: {len(pairs)}")
print(f"min           : {pairs[0]:.4f}")
print(f"median        : {pairs[len(pairs) // 2]:.4f}")
print(f"max           : {pairs[-1]:.4f}")
print(f"mean          : {sum(pairs) / len(pairs):.4f}")
print("\nmost similar UNRELATED pair:")
best = max(
    itertools.combinations(range(len(unrelated)), 2),
    key=lambda p: cos(vecs[p[0]], vecs[p[1]]),
)
print(f"  {cos(vecs[best[0]], vecs[best[1]]):.4f}  {unrelated[best[0]]!r}")
print(f"          vs  {unrelated[best[1]]!r}")


# ----------------------------------------------- 4. silent truncation past 256 tokens
rule("4. Truncation past max_tokens is silent")
long_text = body
while E.count_tokens(long_text) < 600:
    long_text = long_text + "\n" + body
tok_total = E.count_tokens(long_text)

ids = E._model.tokenizer.encode(long_text, add_special_tokens=False)
head = E._model.tokenizer.decode(ids[:254])
tail = E._model.tokenizer.decode(ids[254:])

print(f"full text tokens        : {tok_total}")
print(f"model ceiling           : {E.max_tokens}")
print(f"cos(full, head-254tok)  : {sim(long_text, head):.6f}   <- ~1.0 = tail discarded")
print(f"cos(full, discarded tail): {sim(long_text, tail):.6f}")
print(f"no exception, no warning: embed() returned {len(E.embed([long_text])[0])} floats")


# --------------------------------------------------- 5. dilution: a fact in a big chunk
rule("5. Dilution: the same fact retrieves worse inside a bigger chunk")
query = "when are we shipping the checkout rewrite"
fact = "Priya: We ship the checkout rewrite the Friday after the audit fixes land, not this one."
filler_turns = [t.rendered() for t in turns if "checkout rewrite" not in t.rendered()]

print(f"query: {query!r}\n")
print(f"{'chunk tokens':>14s}  {'cos to query':>12s}   chunk contents")
for n_filler in (0, 2, 4, 8, 12):
    chunk = "\n".join([fact, *filler_turns[:n_filler]])
    print(
        f"{E.count_tokens(chunk):>14d}  {sim(query, chunk):>12.4f}"
        f"   fact + {n_filler} unrelated turns"
    )


# ------------------------------------------------------ 6. a fact split across chunks
rule("6. A fact split across two chunks is a fact neither chunk states")
q = "when do we ship the checkout rewrite"
half_a = "Anita: We should ship the checkout rewrite on Friday."
priya = (
    "Priya: No. We ship the checkout rewrite the Friday after the audit fixes land, not this one."
)
intact = f"{half_a}\n{priya}"
half_b = "Priya: No. We ship it the Friday after the audit fixes land, not this one."
print(f"query: {q!r}\n")
print(f"  intact (both turns)     cos={sim(q, intact):.4f}   answers the question")
print(f"  half A only             cos={sim(q, half_a):.4f}   states the WRONG date, confidently")
print(f"  half B only             cos={sim(q, half_b):.4f}   'it' has no referent")
delta = sim(q, half_a) - sim(q, half_b)
print(f"\n  half A outscores half B by {delta:+.4f}: the wrong half wins.")


# ------------------------------------------------------------ 7. speaker labels
rule("7. Keeping the speaker label inside the chunk")
qs = "what did Sriram say about the export"
without = (
    "The nightly export has been silently truncating at ten thousand rows since the migration."
)
with_label = f"Sriram: {without}"
print(f"query: {qs!r}")
print(f"  with 'Sriram:'    cos={sim(qs, with_label):.4f}")
print(f"  without           cos={sim(qs, without):.4f}")
print(f"  delta             {sim(qs, with_label) - sim(qs, without):+.4f}")


# ------------------------------------- 8. confident nonsense: answer absent vs present
rule("8. The same top-1 score whether or not the answer is there")
corpus_turns = [t.rendered() for t in turns]
cvecs = E.embed(corpus_turns)


def top1(question: str, texts: list[str], vs: list[list[float]]) -> tuple[float, str]:
    qv = E.embed([question])[0]
    scored = sorted(((cos(qv, v), t) for v, t in zip(vs, texts, strict=True)), reverse=True)
    return scored[0]


answerable = "what is happening with the nightly export"
unanswerable = "what did we decide about the pension contribution rates"

for label, question in [("ANSWER PRESENT", answerable), ("ANSWER ABSENT ", unanswerable)]:
    score, text = top1(question, corpus_turns, cvecs)
    print(f"{label}  top1={score:.4f}")
    print(f"                 q: {question!r}")
    print(f"                 -> {text[:88]!r}\n")


# ---------------------------------------------- 9. the chunker on the real tokenizer
rule("9. The repo's chunker, driven by the model's own tokenizer")
chunks = chunk_turns(turns, count_tokens=E.count_tokens, max_tokens=E.max_tokens, overlap_turns=1)
budget = int(E.max_tokens * 0.85)
print(f"max_tokens={E.max_tokens}  budget=0.85x={budget}  overlap_turns=1")
print(f"turns={len(turns)}  chunks={len(chunks)}")
print(f"\n{'ord':>3s}  {'tokens':>6s}  {'<=budget':>8s}  speakers")
for c in chunks:
    fits = c.token_count <= budget
    print(f"{c.ordinal:>3d}  {c.token_count:>6d}  {fits!s:>8s}  {','.join(c.speakers)}")
over = [c for c in chunks if c.token_count > budget]
print(f"\nchunks over budget: {len(over)}  (must be 0, or the model truncates them)")

# overlap actually present?
shared = 0
for a, b in itertools.pairwise(chunks):
    tail_lines = set(a.text.splitlines())
    if tail_lines & set(b.text.splitlines()):
        shared += 1
print(f"adjacent pairs sharing >=1 turn: {shared} of {max(0, len(chunks) - 1)}")


# --------------------------------------------- 10. oversize single turn gets split
rule("10. A single turn longer than the budget is split, not truncated")
monster = Turn("Ravi", " ".join(["the retry logic backs off exponentially and gives up"] * 60))
print(f"one turn, tokens = {E.count_tokens(monster.rendered())}  (budget {budget})")
split = chunk_turns([monster], count_tokens=E.count_tokens, max_tokens=E.max_tokens)
print(f"chunks produced  = {len(split)}")
print(f"token counts     = {[c.token_count for c in split]}")
print(f"all within budget= {all(c.token_count <= budget for c in split)}")


# ------------------------------------- 11. answer present vs absent vs off-topic
rule("11. Scores do not flag a missing answer. Margins nearly do.")
corpus2 = [t.rendered() for t in turns]
vecs2 = E.embed(corpus2)


def topk(q: str, k: int = 3) -> list[tuple[float, str]]:
    qv = E.embed([q])[0]
    return sorted(((cos(qv, v), t) for v, t in zip(vecs2, corpus2, strict=True)), reverse=True)[:k]


CASES = [
    ("present  ", "how many attempts does the retry give up after", "five attempts"),
    ("present  ", "how many accessibility blockers are there", "four blockers"),
    ("present  ", "what is the new alert threshold", "seventy percent"),
    ("ABSENT   ", "how many attempts does the refund retry give up after", "never discussed"),
    ("ABSENT   ", "how many performance blockers are there", "never discussed"),
    ("ABSENT   ", "what is the new error budget", "never discussed"),
    ("ABSENT   ", "who is on call this weekend", "never discussed"),
    ("off-topic", "what did we decide about pension contribution rates", "not the domain"),
]

print(f"{'kind':>9s}  {'top1':>7s}  {'top2':>7s}  {'gap':>7s}  question / retrieved")
print("-" * 100)
present_scores, absent_scores = [], []
for kind, q, _truth in CASES:
    hits = topk(q)
    s1, t1 = hits[0]
    s2 = hits[1][0]
    print(f"{kind:>9s}  {s1:>7.4f}  {s2:>7.4f}  {s1 - s2:>+7.4f}  {q}")
    print(f"{'':>9s}  {'':>7s}  {'':>7s}  {'':>7s}  -> {t1[:86]}")
    if kind.strip() == "present":
        present_scores.append(s1)
    elif kind.strip() == "ABSENT":
        absent_scores.append(s1)

print("\n" + "=" * 100)
print(f"present top1 range : {min(present_scores):.4f} .. {max(present_scores):.4f}")
print(f"ABSENT  top1 range : {min(absent_scores):.4f} .. {max(absent_scores):.4f}")
overlap = max(absent_scores) >= min(present_scores)
print(f"ranges OVERLAP     : {overlap}")
if overlap:
    print("  -> no single cosine threshold separates 'answered' from 'not answered'.")
    print(
        f"  -> a threshold at {min(present_scores):.2f} still admits "
        f"an absent-answer hit of {max(absent_scores):.4f}"
    )
