"""Shared fixtures."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from agent_lab.db import get_session
from agent_lab.main import app
from fakes import FakeSession


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose database dependency is a healthy fake."""

    def _session() -> Iterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[get_session] = _session
    yield TestClient(app)
    app.dependency_overrides.clear()
