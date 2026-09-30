"""Configuration, read once from the environment.

Config is a boundary, so it is validated like any other boundary. A missing or malformed
DATABASE_URL fails at startup with a clear message rather than at the first query.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, populated from the environment or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://agentlab:agentlab@localhost:5433/agentlab"
    app_name: str = "agent-lab"

    # GET /runs/stream. The deadline bounds the whole run, not the gap between events: a
    # budget for work, which is what a caller cares about. A JSON file of scripted replies
    # (the CLI's format) replaces the built-in demo when set.
    stream_timeout_s: float = 30.0
    stream_token_delay_s: float = 0.05
    stream_script: str | None = None


settings = Settings()
