"""The test double must really produce vectors, or every test built on it is testing nothing."""

import math

from agent_lab.embeddings.fake import FakeEmbedder


def test_fake_embedder_emits_unit_vectors_of_the_declared_dimension() -> None:
    embedder = FakeEmbedder()
    [a, b, a_again] = embedder.embed(
        ["send the recording", "include pricing", "send the recording"]
    )

    assert len(a) == embedder.dimension == 384
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, rel_tol=1e-9)
    assert a == a_again, "identical text, identical vector"
    assert a != b
