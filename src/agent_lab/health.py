"""Health checks that answer 'does the system work', not 'is the process alive'.

A 200 that only proves uvicorn is running is worse than no health check, because it makes a
broken deployment look healthy. Each dependency is probed and reported separately.
"""

from typing import Literal, TypedDict

from sqlalchemy import text
from sqlalchemy.orm import Session


class DependencyStatus(TypedDict):
    """One dependency's state, and why it is in that state."""

    status: Literal["ok", "error"]
    detail: str


def check_database(session: Session) -> DependencyStatus:
    """Confirm Postgres answers a query at all."""
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health checks report, they do not raise
        return {"status": "error", "detail": type(exc).__name__}
    return {"status": "ok", "detail": "reachable"}


def check_pgvector(session: Session) -> DependencyStatus:
    """Confirm the vector extension is installed, not merely available.

    Week 8 hybrid retrieval depends on this, and finding out in November is too late.
    """
    try:
        row = session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": type(exc).__name__}
    if row is None:
        return {"status": "error", "detail": "extension 'vector' not installed"}
    return {"status": "ok", "detail": f"vector {row}"}
