"""FastAPI application.

The lifespan disposes the engine on shutdown. Without it, containers linger on SIGTERM and
`docker compose down` takes ten seconds longer than it should.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy.orm import Session

from agent_lab import __version__
from agent_lab.config import settings
from agent_lab.db import engine, get_session
from agent_lab.health import DependencyStatus, check_database, check_pgvector


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Hold resources for the life of the app, and release them on shutdown."""
    yield
    engine.dispose()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/health")
def health(session: SessionDep, response: Response) -> dict[str, object]:
    """Report every dependency separately, and 503 if any of them is down."""
    dependencies: dict[str, DependencyStatus] = {
        "database": check_database(session),
        "pgvector": check_pgvector(session),
    }
    healthy = all(d["status"] == "ok" for d in dependencies.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "degraded",
        "version": __version__,
        "dependencies": dependencies,
    }
