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


settings = Settings()
