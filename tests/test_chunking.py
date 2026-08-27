"""Chunking, with a deterministic token counter.

Uses a word counter rather than a real tokenizer: these tests are about grouping logic, and a
counter you can do in your head makes the expected numbers obvious rather than magic. One test
in the integration suite uses the real model, which is where truncation actually matters.
"""

from agent_lab.ingest.chunking import Turn, chunk_turns, parse_turns


def words(text: str) -> int:
    return len(text.split())


TRANSCRIPT = """[10:00] Ranjan: Morning. Let's start with the metrics dashboard.
Sriram: I pushed the CI fix last night.
It took three attempts because of the heap limit.
Priya (10:02): Can we ship on Friday?
Ranjan: I want to hold it.
"""


def test_parsing_handles_the_shapes_real_exports_produce() -> None:
    turns = parse_turns(TRANSCRIPT)
    assert [t.speaker for t in turns] == ["Ranjan", "Sriram", "Priya", "Ranjan"]
    # A bare continuation line belongs to whoever was speaking. Starting a new turn would
    # invent a speaker change; dropping it would lose content.
    assert "three attempts" in turns[1].text
    # The leading timestamp is stripped from the speaker, not baked into the name.
    assert turns[0].speaker == "Ranjan"


def test_an_unlabelled_transcript_still_produces_one_turn() -> None:
    turns = parse_turns("just some prose with no speakers at all")
    assert len(turns) == 1
    assert turns[0].speaker is None
    assert turns[0].rendered() == "just some prose with no speakers at all"


def test_the_speaker_label_is_inside_the_chunk_text() -> None:
    """It is embedded with the words, so a question naming a person can match on the name."""
    chunks = chunk_turns(parse_turns(TRANSCRIPT), count_tokens=words, max_tokens=1000)
    assert "Sriram: I pushed the CI fix" in chunks[0].text


def test_no_chunk_exceeds_the_budget() -> None:
    """The model truncates past its ceiling in silence, so overrunning is never acceptable."""
    turns = parse_turns(TRANSCRIPT)
    for max_tokens in (8, 12, 20, 40):
        budget = int(max_tokens * 0.85)
        chunks = chunk_turns(turns, count_tokens=words, max_tokens=max_tokens)
        oversize = [c for c in chunks if c.token_count > budget]
        assert not oversize, f"max_tokens={max_tokens}: {[c.token_count for c in oversize]}"


def test_no_chunk_is_a_duplicate_or_a_subset_of_another() -> None:
    """A chunk contained in another competes with its own parent in every search.

    The first implementation produced exactly this: after emitting, it restarted from the
    overlap tail without checking the new group fit, so the group never shrank and the next
    chunk was both over budget and a superset of the previous one.
    """
    chunks = chunk_turns(parse_turns(TRANSCRIPT), count_tokens=words, max_tokens=12)
    texts = [c.text for c in chunks]
    assert len(texts) == len(set(texts))
    for i, a in enumerate(texts):
        for j, b in enumerate(texts):
            assert i == j or a not in b, f"chunk {i} is contained in chunk {j}"


def test_overlap_carries_a_turn_forward_when_there_is_room() -> None:
    """A decision often lands one turn after the context explaining it.

    With room in the budget the tail is carried, so at least one chunk holds both.
    """
    turns = [Turn("A", f"turn {i} words here") for i in range(6)]
    chunks = chunk_turns(turns, count_tokens=words, max_tokens=13, overlap_turns=1)

    assert len(chunks) > 1
    carried = [
        i
        for i in range(1, len(chunks))
        if chunks[i].text.split("\n")[0] == chunks[i - 1].text.split("\n")[-1]
    ]
    assert carried, "no chunk began with the previous chunk's last turn"


def test_overlap_is_given_up_rather_than_blowing_the_budget() -> None:
    """When the tail plus the next turn cannot fit, the tail is dropped, not carried."""
    turns = [Turn("A", " ".join(["word"] * 8)) for _ in range(4)]
    budget = int(10 * 0.85)
    chunks = chunk_turns(turns, count_tokens=words, max_tokens=10, overlap_turns=1)
    assert all(c.token_count <= budget for c in chunks)


def test_a_single_over_long_turn_is_split_on_sentence_boundaries() -> None:
    """Rare but real: someone reads a document aloud. Splitting beats being truncated."""
    long_turn = Turn("A", "One two three. Four five six. Seven eight nine. Ten eleven twelve.")
    chunks = chunk_turns([long_turn], count_tokens=words, max_tokens=8)
    assert len(chunks) > 1
    # Sentences stay whole: a split mid-sentence would retrieve as a fragment.
    assert all(c.text.count(".") >= 1 for c in chunks)
