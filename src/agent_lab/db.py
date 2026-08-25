"""Database engine and session factory.

The engine is created once at import and disposed in the app lifespan. Creating an engine
per request is the classic mistake: it opens a new pool every time and exhausts Postgres.
"""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from agent_lab.config import settings

engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a session and always close it, even when the request raises."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
