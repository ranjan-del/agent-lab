"""Test doubles.

FakeSession stands in for a SQLAlchemy session so the health tests never need a live
database. It mocks at the boundary (the session), not at the internals of the health module,
which is the difference between a test that catches regressions and one that only passes.
"""


class FakeSession:
    """A session stand-in with two switches: what pgvector reports, and whether it fails."""

    def __init__(self, vector_version: str | None = "0.8.6", fail: bool = False) -> None:
        self.vector_version = vector_version
        self.fail = fail

    def execute(self, statement: object) -> "FakeSession":
        if self.fail:
            raise RuntimeError("connection refused")
        return self

    def scalar_one_or_none(self) -> str | None:
        return self.vector_version
