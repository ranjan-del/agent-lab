"""Split a transcript into passages that are worth retrieving.

Chunking is the least glamorous and most consequential decision in a retrieval system. It is
made once, at ingest, and it silently caps how good retrieval can ever be: a fact split across
two chunks is a fact neither chunk fully states, and no reranker downstream can put it back.

Four rules, each with a reason:

1. **Never split a speaker turn.** "Sriram: we should ship on Friday" is the unit of meaning
   in a transcript. Half of it retrieves as something its speaker did not say.
2. **Keep speaker labels inside the chunk text.** They are embedded along with the words, so
   "what did Sriram say about shipping" can match on the name as well as the topic.
3. **Budget in the embedding model's OWN tokens, with headroom.** The model truncates past its
   ceiling without complaining, so a chunk that overruns embeds as a different chunk than the
   one you stored.
4. **Overlap by whole turns.** A decision often lands one turn after the context that explains
   it. Overlap means at least one chunk holds both.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# "Ranjan: text", "Ranjan (14:03): text", "[14:03] Ranjan: text". Loose on purpose: transcript
# exports vary, and a line that does not match is treated as a continuation of the last turn
# rather than dropped.
_SPEAKER = re.compile(
    r"^\s*(?:\[?(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\]?\s*)?"
    r"(?P<speaker>[A-Z][\w .'-]{0,60}?)"
    r"(?:\s*\((?P<paren_time>[^)]{0,20})\))?\s*:\s+"
    r"(?P<text>\S.*)$"
)


@dataclass(frozen=True, slots=True)
class Turn:
    """One continuous stretch of speech by one person."""

    speaker: str | None
    text: str

    def rendered(self) -> str:
        """How the turn appears inside a chunk, and therefore what gets embedded."""
        return f"{self.speaker}: {self.text}" if self.speaker else self.text


@dataclass(frozen=True, slots=True)
class Chunk:
    """A retrievable passage, with the turns it came from."""

    ordinal: int
    text: str
    token_count: int
    speakers: tuple[str, ...]


def parse_turns(raw_text: str) -> list[Turn]:
    """Split a transcript into speaker turns.

    Consecutive lines from the same speaker are merged, because an export that wraps long
    speech across lines would otherwise produce dozens of one-line turns and destroy the
    structure chunking depends on.
    """
    turns: list[Turn] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _SPEAKER.match(stripped)
        if match:
            speaker = match.group("speaker").strip()
            text = match.group("text").strip()
            if turns and turns[-1].speaker == speaker:
                turns[-1] = Turn(speaker, f"{turns[-1].text} {text}")
            else:
                turns.append(Turn(speaker, text))
        elif turns:
            # An unlabelled line continues whoever was speaking. Dropping it would lose
            # content; starting a new turn would invent a speaker change that never happened.
            turns[-1] = Turn(turns[-1].speaker, f"{turns[-1].text} {stripped}")
        else:
            turns.append(Turn(None, stripped))
    return turns


def chunk_turns(
    turns: Sequence[Turn],
    *,
    count_tokens: Callable[[str], int],
    max_tokens: int,
    overlap_turns: int = 1,
) -> list[Chunk]:
    """Group turns into chunks that fit the model's ceiling.

    ``max_tokens`` should be the model's real limit; the headroom is taken here rather than
    asked of the caller, so nobody has to remember to leave room for special tokens.

    The overlap has a sharp edge worth naming. After emitting a chunk, the next one restarts
    from the tail of the one just emitted. If that tail plus the incoming turn still does not
    fit, the tail must be given up rather than carried, or the group never shrinks and the next
    emitted chunk is both over budget and a superset of the one before it.
    """
    budget = max(1, int(max_tokens * 0.85))

    chunks: list[Chunk] = []
    current: list[Turn] = []

    def render(group: Sequence[Turn]) -> str:
        return "\n".join(t.rendered() for t in group)

    def emit(group: Sequence[Turn]) -> None:
        text = render(group)
        if chunks and chunks[-1].text == text:
            return
        speakers = tuple(dict.fromkeys(t.speaker for t in group if t.speaker))
        chunks.append(Chunk(len(chunks), text, count_tokens(text), speakers))

    for turn in turns:
        for piece in _fit(turn, count_tokens, budget):
            if not current:
                current = [piece]
                continue

            candidate = [*current, piece]
            if count_tokens(render(candidate)) <= budget:
                current = candidate
                continue

            emit(current)

            # Restart from the tail of what was just emitted, shrinking it until the new group
            # fits. Without this the tail is carried forever and every later chunk grows.
            tail = list(current[-overlap_turns:]) if overlap_turns else []
            while tail and count_tokens(render([*tail, piece])) > budget:
                tail.pop(0)
            current = [*tail, piece]

    if current:
        emit(current)

    return chunks


def _fit(turn: Turn, counter: Callable[[str], int], budget: int) -> list[Turn]:
    """Split one over-long turn until every piece fits. Guarantees the budget is never exceeded.

    A turn longer than the whole budget is rare but real: someone reads a document aloud, or an
    export merges an entire monologue into one line. Splitting it is worse than not having to,
    and far better than the alternative, which is not a choice: the model truncates silently, so
    an oversized chunk embeds as something other than the text stored beside it, and retrieval
    degrades for a reason nothing reports.

    Sentence boundaries are preferred because a fragment retrieves badly. When there are none,
    or when a single sentence is still too long, it falls back to a hard split on words. Ugly,
    and still strictly better than being truncated.
    """
    if counter(turn.rendered()) <= budget:
        return [turn]

    pieces: list[Turn] = []
    for sentence_group in _split_greedily(
        turn, re.split(r"(?<=[.!?])\s+", turn.text), counter, budget
    ):
        if counter(sentence_group.rendered()) <= budget:
            pieces.append(sentence_group)
            continue
        # A single sentence still too long: split on words as a last resort.
        pieces.extend(_split_greedily(turn, sentence_group.text.split(), counter, budget))
    return pieces or [turn]


def _split_greedily(
    turn: Turn, parts: Sequence[str], counter: Callable[[str], int], budget: int
) -> list[Turn]:
    """Pack parts into as few turns as fit, preserving order and the speaker label."""
    out: list[Turn] = []
    buffer = ""
    for part in parts:
        candidate = f"{buffer} {part}".strip()
        if buffer and counter(Turn(turn.speaker, candidate).rendered()) > budget:
            out.append(Turn(turn.speaker, buffer))
            buffer = part
        else:
            buffer = candidate
    if buffer:
        out.append(Turn(turn.speaker, buffer))
    return out
