# app/config.py — typed settings, loaded once from the environment.
import os
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Secrets come from the environment / Render env groups.

    Nothing here should ever be logged. Treat every field as sensitive.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- OpenAI ---------------------------------------------------------------
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    chat_model: str = Field(default="gpt-4o", alias="ATLAS_CHAT_MODEL")
    embedding_model: str = Field(
        default="text-embedding-3-small", alias="ATLAS_EMBEDDING_MODEL"
    )
    embedding_dim: int = Field(default=1536, alias="ATLAS_EMBEDDING_DIM")

    # --- Database -------------------------------------------------------------
    database_url: str = Field(
        default="postgresql://localhost:5432/atlas", alias="DATABASE_URL"
    )

    # --- Google OAuth ---------------------------------------------------------
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(
        default="http://localhost:8000/oauth/callback", alias="GOOGLE_REDIRECT_URI"
    )

    # --- Token encryption (Fernet) -------------------------------------------
    token_enc_key: str = Field(default="", alias="TOKEN_ENC_KEY")

    # --- Langfuse observability ----------------------------------------------
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", alias="LANGFUSE_HOST"
    )

    # --- App behaviour --------------------------------------------------------
    # Single-user v1: the one sandbox account this instance serves.
    default_user_id: str = Field(default="me", alias="ATLAS_DEFAULT_USER_ID")
    work_hours_start: int = Field(default=9, alias="ATLAS_WORK_HOURS_START")
    work_hours_end: int = Field(default=18, alias="ATLAS_WORK_HOURS_END")
    # IANA timezone name (e.g. "Asia/Kolkata", "America/New_York"). All relative
    # dates the agent reasons about and all proposed slots/events use this zone.
    timezone: str = Field(default="UTC", alias="ATLAS_TIMEZONE")


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Cached accessor so we parse the environment exactly once.

    pydantic-settings reads `.env` into this model, but third-party libraries
    (the OpenAI Agents SDK client, its trace exporter, Langfuse) read secrets
    straight from `os.environ`. Mirror the key vars into the process env so they
    work too. `setdefault` means a real env var always wins over `.env`.
    """
    s = Settings()
    if s.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", s.openai_api_key)
    return s


def app_tz() -> ZoneInfo:
    """The configured application timezone. Falls back to UTC if the name is
    invalid so a typo can't crash scheduling."""
    try:
        return ZoneInfo(settings().timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")
