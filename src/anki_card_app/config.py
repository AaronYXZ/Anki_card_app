from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
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
    auth_mode: Literal["development", "password"] = Field(
        default="development", validation_alias="AUTH_MODE"
    )
    session_cookie_name: str = Field(default="anki_session", validation_alias="SESSION_COOKIE_NAME")
    csrf_cookie_name: str = Field(default="anki_csrf", validation_alias="CSRF_COOKIE_NAME")
    session_lifetime_days: int = Field(default=30, gt=0, validation_alias="SESSION_LIFETIME_DAYS")
    session_cookie_secure: bool = Field(default=False, validation_alias="SESSION_COOKIE_SECURE")
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
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.6-terra", validation_alias="OPENAI_MODEL")
    openai_timeout_seconds: float = Field(
        default=90.0,
        validation_alias="OPENAI_TIMEOUT_SECONDS",
    )
    openai_max_retries: int = Field(default=0, validation_alias="OPENAI_MAX_RETRIES")
    max_upload_bytes: int = Field(default=10_000_000, validation_alias="MAX_UPLOAD_BYTES")
    max_archive_files: int = Field(default=250, validation_alias="MAX_ARCHIVE_FILES")
    max_archive_uncompressed_bytes: int = Field(
        default=50_000_000,
        validation_alias="MAX_ARCHIVE_UNCOMPRESSED_BYTES",
    )

    @model_validator(mode="after")
    def validate_production_authentication(self) -> Self:
        if self.app_env == "production" and self.auth_mode != "password":
            raise ValueError("Production requires AUTH_MODE=password.")
        if self.app_env == "production" and not self.session_cookie_secure:
            raise ValueError("Production requires SESSION_COOKIE_SECURE=true.")
        lowered_database_url = self.database_url.casefold()
        if self.app_env == "production" and (
            lowered_database_url.startswith("sqlite:")
            or "@localhost" in lowered_database_url
            or "@127.0.0.1" in lowered_database_url
        ):
            raise ValueError("Production requires a non-local PostgreSQL DATABASE_URL.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
