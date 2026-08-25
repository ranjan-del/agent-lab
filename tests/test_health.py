"""Health endpoint behaviour, without a database.

These tests exist because a health check that cannot fail is not a health check.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_lab.db import get_session
from agent_lab.main import app
from fakes import FakeSession


def test_health_reports_ok_when_everything_works(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"]["database"]["status"] == "ok"
    assert body["dependencies"]["pgvector"]["status"] == "ok"


@pytest.mark.parametrize(
    ("session", "expected_detail"),
    [
        (FakeSession(vector_version=None), "extension 'vector' not installed"),
        (FakeSession(fail=True), "RuntimeError"),
    ],
    ids=["pgvector-missing", "database-unreachable"],
)
def test_health_degrades_and_returns_503(session: FakeSession, expected_detail: str) -> None:
    """Table-driven, because this is the exact shape the week 4 eval harness takes."""

    def _session() -> Iterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = _session
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    details = [d["detail"] for d in response.json()["dependencies"].values()]
    assert expected_detail in details
