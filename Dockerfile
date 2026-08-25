# Single stage on purpose. Turning this into a multi-stage build under 200 MB, running as a
# non-root user with correct signal handling, is the deferred half of module 3 and belongs in
# the weekday slot across W1 to W4. It must be done before the W11 deploy.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, source second: the layer cache keys on file contents, so a source edit
# does not reinstall every package.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --no-dev

# 0.0.0.0, not 127.0.0.1. Binding to loopback inside a container makes the port unreachable
# from the host even though `docker ps` shows it published. This is the classic trap.
CMD ["uvicorn", "agent_lab.main:app", "--host", "0.0.0.0", "--port", "8000"]
