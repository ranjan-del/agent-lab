# Embeddings, similarity, and why a bad chunk answers confidently

Week 2 fundamentals. Every number below was measured on this machine on 2026-09-08 with the
model this project actually uses, `sentence-transformers/all-MiniLM-L6-v2`, driven through
`agent_lab.embeddings.local` and `agent_lab.ingest.chunking`. The measurement script is
`docs/notes/scripts/2026-09-08-embeddings.py`; re-run it with
`uv run --group embed python docs/notes/scripts/2026-09-08-embeddings.py`.

The transcript under test is synthetic. This repository is public and the SPEC forbids
organisational content, so I wrote a seventeen-turn standup with the shape of a real one:
interruptions, a correction, pronouns whose referent is a turn away, and one decision that
reverses another. The shape is what the measurements depend on, not the content.

## 1. What the model is

| Property | Value | Consequence |
|---|---|---|
| Dimension | 384 | the pgvector column is `vector(384)`; changing model means a migration |
| `max_seq_length` | 256 tokens | the hard ceiling, and the number the chunker budgets against |
| Vectors normalised at source | norm = 1.000000 | cosine distance is a plain dot product |
| Identical text | cosine = 1.000000 | exactly, so equality is a usable check |

Because vectors are unit length, everything below is a dot product, and pgvector's
`vector_cosine_ops` agrees with anything I compute in Python. That is worth pinning once: if
the vectors were not normalised, the numbers in this note and the numbers the database
returns would quietly differ.

## 2. Transcript text tokenizes worse than prose

Measured on the seventeen-turn transcript, 1,760 characters:

| Measure | Value |
|---|---|
| Words | 302 |
| Tokens | 413 |
| Tokens per word | 1.368 |
| Characters per token | 4.262 |

The per-line detail is where the cost hides:

| Line kind | Words | Tokens | Tokens/word |
|---|---|---|---|
| Plain prose | 13 | 18 | 1.38 |
| Same line with a speaker label | 14 | 21 | 1.50 |
| Names and numbers (`Sriram`, `10000`, `12th`) | 11 | 17 | 1.55 |

A speaker label costs about three tokens and roughly 9% on the ratio. Section 7 shows it
buys far more than it costs, but the budget has to know about it, which is why the chunker
counts `Turn.rendered()` and not the raw text.

**The rule this justifies:** never budget in words. A word-count estimate here runs 27% under
the true count, and an estimate that runs under is exactly how a chunk gets truncated while
the budget check passes.

## 3. Cosine has no absolute meaning

Eight deliberately unrelated sentences, 28 pairs:

| Statistic | Value |
|---|---|
| Minimum | -0.1034 |
| Median | 0.0406 |
| Mean | 0.0600 |
| Maximum | 0.3102 |

The most similar *unrelated* pair, at 0.3102:

> "The nightly export truncated at ten thousand rows."
> "The staging database is out of disk again."

Nothing connects them except register: both are infrastructure complaints in the same clipped
voice. The model is not wrong, it is answering a different question than the one I care about.
It measures *how alike two passages read*, and I keep wanting it to measure *does this passage
answer my question*. Those come apart, and section 6 is where they come apart badly.

So 0.31 is not "31% relevant". It is a number whose meaning depends entirely on this model and
this kind of text, and the only way to know what counts as high is to measure the floor, which
is what this section is.

## 4. Truncation past 256 tokens is silent

I concatenated the transcript with itself until it was well past the ceiling, then compared
the full text against its own first 254 tokens:

| Comparison | Cosine |
|---|---|
| Full 824-token text vs its first 254 tokens | **1.000000** |
| Full 824-token text vs the 570 tokens that were cut | 0.756025 |

A cosine of exactly 1.000000 against the head is the proof: the model embedded the head and
discarded 69% of the input. `embed()` raised nothing, warned nothing, and returned a perfectly
well-formed 384-float vector.

One nuance worth recording, because it misled me for a minute. The HuggingFace tokenizer *does*
print `Token indices sequence length is longer than the specified maximum (413 > 256)` when you
call it directly, which is what `count_tokens` does. The `encode()` path that actually produces
the vector prints nothing. So the warning appears when you *measure*, and vanishes when you
*embed*. Anyone reading logs would conclude the opposite of the truth.

This is the failure mode the chunker exists to prevent: an over-long chunk embeds as something
other than the text stored beside it, retrieval degrades, and nothing anywhere reports it.

## 5. Dilution: the same fact retrieves worse in a bigger chunk

Query: *"when are we shipping the checkout rewrite"*. The chunk always contains the sentence
that answers it. The only thing that changes is how much unrelated material sits alongside.

| Chunk tokens | Cosine | Contents |
|---|---|---|
| 25 | **0.5185** | the answer alone |
| 119 | 0.3972 | answer + 2 unrelated turns |
| 143 | 0.3857 | answer + 4 unrelated turns |
| 222 | 0.2941 | answer + 8 unrelated turns |
| 344 | 0.2798 | answer + 12 unrelated turns (over budget, so also truncated) |

A single embedding is an average of everything in the passage. Padding the answer with
unrelated turns drags the vector toward the padding, and the score falls by 46% without a
single word of the answer changing.

This is the argument against "just use big chunks so you never split anything". Big chunks do
not lose facts, they *dilute* them, and a diluted fact loses the ranking to a shorter chunk
that is merely on-topic. Both failure modes are real; the chunk size is the trade, not a
setting with a right answer.

## 6. The finding I did not expect

A fact split across two chunks is the thing the chunker's rule 4 exists to prevent. I expected
to measure a modest penalty. What I measured is worse than a penalty.

The exchange is a correction. Anita proposes Friday, Priya overrules her:

> Anita: We should ship the checkout rewrite on Friday.
> Priya: No. We ship the checkout rewrite the Friday after the audit fixes land, not this one.

Query: *"when do we ship the checkout rewrite"*.

| Chunk | Cosine | What it actually says |
|---|---|---|
| Half A alone | **0.7498** | Friday. **Wrong.** The proposal that was overruled |
| Both turns intact | 0.7046 | the Friday after. Correct |
| Half B alone | 0.4050 | "it" has no referent; unusable on its own |

**The wrong half outranks the correct chunk by +0.0452, and outranks the other half by
+0.3448.** Split this exchange and top-1 retrieval returns the overruled proposal, with the
highest score in the set, and the agent tells you Friday.

That is the whole of "why cosine similarity on a bad chunk returns confident nonsense", and
the mechanism is not subtle once you see it. Half A is *shorter*, *more on-topic per token*,
and *contains no hedging*. Every property that makes it score well is a property of a clean
retrieval hit. The correct chunk is longer, carries a negation, and spends half its tokens on
the proposal being rejected. Similarity rewards the confident wrong answer for being confident.

No reranker fixes this, because the reranker sees the same two chunks. Nothing downstream can
reassemble a fact that ingestion took apart. The decision was made once, at chunk time, and it
capped how good retrieval could ever be on this question.

## 7. Keeping the speaker label inside the chunk

Query: *"what did Sriram say about the export"*.

| Chunk text | Cosine |
|---|---|
| `Sriram: The nightly export has been silently truncating...` | **0.5840** |
| Same sentence, label stripped | 0.2425 |

**+0.3415** for three tokens. Attribution questions are most of what I will ask a meeting
agent, and the name has to be inside the embedded text to be searchable at all. Storing the
speaker only in a metadata column would make "what did Sriram say" a filter I have to know to
apply, rather than something the query text can express on its own.

## 8. Scores do not tell you the answer is missing. Margins nearly do.

Eight questions against the seventeen turns, retrieved individually. Three have answers in the
transcript, four are in-domain but were never discussed, one is off-topic.

| Kind | Top-1 | Top-2 | Margin | Question |
|---|---|---|---|---|
| present | 0.5482 | 0.3289 | +0.2193 | how many attempts does the retry give up after |
| present | 0.5588 | 0.2341 | +0.3247 | how many accessibility blockers are there |
| present | 0.5708 | 0.1671 | +0.4036 | what is the new alert threshold |
| **absent** | **0.5443** | 0.4170 | +0.1273 | how many attempts does the **refund** retry give up after |
| absent | 0.2721 | 0.2097 | +0.0624 | how many **performance** blockers are there |
| absent | 0.2709 | 0.2386 | +0.0322 | what is the new error budget |
| absent | 0.2367 | 0.2321 | +0.0046 | who is on call this weekend |
| off-topic | 0.0782 | 0.0777 | +0.0006 | pension contribution rates |

The off-topic question is easy: 0.078, any threshold catches it. The dangerous row is the
fourth. *"How many attempts does the refund retry give up after"* is a question about refunds.
The transcript discusses a **payment webhook** retry that gives up after five, and refunds only
in passing as something already done. It scores **0.5443**, retrieves the webhook turn, and an
agent handed that chunk will answer "five attempts" about a retry that was never discussed.

| Band | Range |
|---|---|
| Top-1, answer present | 0.5482 .. 0.5708 |
| Top-1, answer absent | 0.2367 .. **0.5443** |

The bands are separated by **0.0039**. They technically do not overlap, and treating that as a
usable threshold would be self-deception: 0.004 on seven cases and one transcript is noise, and
the next transcript closes it.

The margin column is the better signal, and this is the practical finding of the week:

| Band | Range |
|---|---|
| Margin, answer present | +0.2193 .. +0.4036 |
| Margin, answer absent | +0.0006 .. +0.1273 |

Separated by 0.092, more than twenty times the gap between the raw-score bands. The intuition
holds up: when the answer is really there, one chunk is *distinctively* the right one and the
runner-up falls away. When it is absent, the top few are a tie among things that merely share
vocabulary, and the flatness of the tail is the tell.

Seven cases on one synthetic transcript is a hypothesis, not a law. Writing it down as a number
now is the point: W4 builds the eval harness, and this is the first thing it should try to
falsify.

## 9. The chunker, driven by the model's own tokenizer

`chunk_turns` on the seventeen turns, with `count_tokens` from the real model:

| Setting | Value |
|---|---|
| `max_tokens` | 256 (read from the model, not hard-coded) |
| Budget, 0.85x | 217 |
| `overlap_turns` | 1 |

| Chunk | Tokens | Within budget | Speakers |
|---|---|---|---|
| 0 | 199 | yes | Priya, Ravi, Sriram |
| 1 | 211 | yes | Sriram, Priya, Anita, Ravi |
| 2 | 42 | yes | Ravi, Priya |

Chunks over budget: **0**. Adjacent pairs sharing at least one turn: **2 of 2**, so the overlap
is real and not just intended.

The 15% headroom is not superstition. The budget is checked against `count_tokens`, which
includes special tokens, but chunk text is assembled from rendered turns joined with newlines,
and the join is not free. Headroom absorbs the difference so a boundary case cannot land at 257.

And the case that has to work, or rule 3 is a lie:

| Input | Tokens | Result |
|---|---|---|
| One turn, someone reading a document aloud | 664 | split into 4 chunks: 217, 217, 217, 25 |

All four within budget, none truncated. A turn longer than the whole budget cannot be kept
whole, so the choice is to split it or let the model silently eat the tail. Splitting is worse
than not having to. It is much better than section 4.

## What I would tell someone starting this

| Claim | The number behind it |
|---|---|
| Budget in the model's tokens, never in words | word count runs 27% under on transcript text |
| A cosine score has no meaning without a measured floor | unrelated pairs reach 0.3102 |
| Truncation is silent, so the chunker must guarantee the ceiling | cos(full, head) = 1.000000 |
| Chunk size trades dilution against splitting | 0.5185 down to 0.2798 as filler grows |
| Splitting is the worse half of that trade | the overruled proposal beats the real decision, 0.7498 vs 0.7046 |
| Speaker labels earn their tokens many times over | +0.3415 for 3 tokens |
| Do not threshold on the top-1 score | present and absent bands sit 0.0039 apart |
| Consider thresholding on the top1-to-top2 margin | bands sit 0.092 apart, to be tested properly in W4 |

The thing I got wrong going in: I assumed "confident nonsense" meant the retriever returning
junk with a misleadingly high score for a question it could not answer. Sometimes it does. But
the sharper failure is section 6, where retrieval works exactly as designed, the score is
genuinely high, the chunk is genuinely the closest match, and the answer is still wrong,
because a decision was cut in half at ingest and the discarded half was the one that mattered.
The retriever was never the problem. The chunker was, and it had already finished its work
days earlier.
