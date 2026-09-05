# Tokens, context windows, and what one week of my meetings costs

Week 1 fundamentals. Every number below is either measured on this machine, read from the
vendor's own pricing page on 2026-09-05, or derived from those two with the arithmetic shown.

## 1. A token is not a word

| Fact | Evidence |
|---|---|
| The embedding model here (all-MiniLM-L6-v2, WordPiece) turned 155 words / 901 characters of transcript into 256 tokens | measured on the one real transcript in the dev database: 1.65 tokens per word, 3.5 characters per token |
| Transcripts tokenize worse than prose | speaker labels, timestamps and short turns are all extra tokens with no meaning |
| Different models, different counts | Anthropic's pricing FAQ gives roughly 4 characters or 0.75 words per token for English, and says the Claude 4.7+ tokenizer emits about 30% more tokens for the same text than the previous one |
| The count you pay for is the model's count, not yours | always measure with the target tokenizer; never estimate from word counts in production |

## 2. Context window: what fills it

| What goes in | Who controls it |
|---|---|
| System prompt and tool definitions | me, fixed per request, the best caching candidate |
| Retrieved chunks (RAG) | the retriever: k chunks times chunk size |
| Conversation history | grows every turn until something evicts it |
| The model's own output so far | the model |
| Per-model overhead, for example 354 extra tokens when tools are enabled on Sonnet 5 | the vendor |

Nothing is "evicted" by the API: when the window is full the request fails. Eviction is
something the application does, by summarising or dropping history. That is a design decision,
not a default.

## 3. Input vs output pricing

From platform.claude.com/docs/en/about-claude/pricing, 2026-09-05, USD per million tokens:

| Model | Input | Output | Output / input |
|---|---|---|---|
| Claude Haiku 4.5 | $1 | $5 | 5x |
| Claude Sonnet 5 | $2 | $10 | 5x |
| Claude Opus 5 | $5 | $25 | 5x |

Output costs five times input on every current model. A verbose answer is the expensive part
of a request, which is why "answer in under 100 words" is a cost control, not a style choice.

## 4. Prompt caching: what it does to the bill

| Rule | Value (same page) |
|---|---|
| Cache write, 5-minute lifetime | 1.25x the input price |
| Cache write, 1-hour lifetime | 2x the input price |
| Cache read | 0.1x the input price |
| Minimum cacheable prefix | 1,024 tokens on Sonnet 5, 512 on Opus 5, 4,096 on Haiku 4.5 |
| Break-even | one read pays back a 5-minute write; two reads pay back a 1-hour write |

The only thing worth caching in this project is the system prompt plus tool schemas, and only
once it is above the model's minimum. Retrieved chunks change per request and never hit.

## 5. The number: one week of my real transcript volume

Calendar for Mon 31 Aug to Sun 6 Sep 2026, read from Google Calendar:

| Measure | Value | How |
|---|---|---|
| Meetings | 11 | 5 team lunches, 4 one-to-ones, 1 leadership weekly, 1 feature discussion |
| Total meeting time | 525 min | summed from the events |
| Meetings that produced a recording or Gemini notes | 6 | attachments on the events |
| Transcribed time | 300 min | those six |
| Words spoken | 39,000 | 300 min x 130 words per minute, a conservative conversational rate |
| Tokens, embedding model | 64,000 | 39,000 x 1.65 measured above |
| Tokens, Claude tokenizer | 52,000 | 39,000 / 0.75 |
| Chunks | about 340 | chunk budget is 0.85 x 256 = 217 tokens, minus a one-turn overlap, so about 190 new tokens per chunk |

### Cost to embed

| Route | Price | Week | Year |
|---|---|---|---|
| Local all-MiniLM-L6-v2 on this laptop | $0 per token, CPU time only | $0 | $0 |
| OpenAI text-embedding-3-small | $0.02 per MTok | $0.0013 | $0.07 |
| OpenAI text-embedding-3-large | $0.13 per MTok | $0.0084 | $0.44 |

Embedding is free to a rounding error at this volume, and stays under a dollar a year at 100x.

### Cost to store

| Measure | Value | How |
|---|---|---|
| One full chunk row, heap | 3,260 bytes | measured with pg_column_size: 800 text, 1,540 embedding (384 x 4-byte floats plus header), 828 tsvector |
| Index share per row (HNSW on the vector, GIN on the tsvector) | about 2,500 bytes | HNSW entries are roughly the vector size plus graph links |
| Per chunk, all in | about 6 KB | |
| Per week | about 2 MB | 340 chunks x 6 KB |
| Per year | about 105 MB | |

A year of every meeting I attend fits in the free tier of any managed Postgres.

### Where the money actually goes

The agent will spend on LLM calls, not on ingestion. One question answered over 10 retrieved
chunks, on Sonnet 5:

| Part | Tokens | Price | Cost |
|---|---|---|---|
| System prompt and tool schemas | 1,500 | $2 / MTok | $0.0030 |
| 10 chunks of context | 2,000 | $2 / MTok | $0.0040 |
| Question and history | 300 | $2 / MTok | $0.0006 |
| Answer | 300 | $10 / MTok | $0.0030 |
| Total | | | about $0.011 per question |

200 questions a week is about $2.10. With the 1,500-token system prompt cached (it clears
Sonnet 5's 1,024 minimum), that part drops from $0.0030 to $0.0003 per question and the
week to about $1.55. Ingesting the same week's transcripts cost $0.0013 on the most expensive
embedding route. The ratio is over a thousand to one: optimise the prompt, not the pipeline.
