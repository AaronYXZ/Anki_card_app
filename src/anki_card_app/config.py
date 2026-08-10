from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(default="development", validation_alias="APP_ENV")
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    database_url: str = Field(
        default="postgresql+psycopg://anki:anki@localhost:5433/anki_card_app",
        validation_alias="DATABASE_URL",
    )
    development_user_id: UUID = Field(
        default=UUID("00000000-0000-0000-0000-000000000001"),
        validation_alias="DEVELOPMENT_USER_ID",
    )
    development_user_email: str = Field(
        default="developer@localhost",
        validation_alias="DEVELOPMENT_USER_EMAIL",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
