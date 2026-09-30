"""Shared fixtures."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from agent_lab.config import settings
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


# ---------------------------------------------------------------------------------------------
# Database fixtures, shared by every test that needs a real Postgres. Moved here from
# test_ingest_integration.py when the agent tools needed them too.
# ---------------------------------------------------------------------------------------------
def _test_database_url() -> str:
    """A dedicated database, so the suite never sees or destroys ingested dev data.

    Tests that only pass on an empty database are fragile: they start failing the first time
    someone runs a real import, which is exactly when you least want a false alarm.
    """
    return os.environ.get("TEST_DATABASE_URL") or settings.database_url + "_test"


def alembic_config_for(url: str) -> Config:
    """The same alembic wiring the engine fixture uses, for tests that migrate on purpose."""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def alembic_config(engine) -> Config:
    """Alembic pointed at the migrated test database. Depends on ``engine`` so head is applied."""
    return alembic_config_for(_test_database_url())


@pytest.fixture(scope="session")
def engine():
    """Create the test database if needed and migrate it exactly the way production is.

    Migrations rather than ``create_all``: this way the suite exercises the same path a real
    deploy takes, so a broken migration fails here instead of at 2am.
    """
    admin_url = settings.database_url
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            name = _test_database_url().rsplit("/", 1)[-1]
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).scalar_one_or_none()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        admin.dispose()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no database reachable: {type(exc).__name__}: {exc}")

    config = alembic_config_for(_test_database_url())
    os.environ["DATABASE_URL"] = _test_database_url()
    command.upgrade(config, "head")

    engine = create_engine(_test_database_url(), pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    """A session on its own transaction, rolled back afterwards.

    Rollback rather than TRUNCATE, so tests cannot see each other's rows and the order they
    run in cannot change the result.
    """
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
