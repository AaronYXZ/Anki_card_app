import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from anki_card_app.config import Settings


def test_settings_accept_environment_overrides(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_DEBUG", "true")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.debug is True


def test_production_requires_password_auth_and_secure_cookie() -> None:
    with pytest.raises(ValidationError, match="AUTH_MODE=password"):
        Settings(app_env="production")
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE=true"):
        Settings(app_env="production", auth_mode="password")

    settings = Settings(
        app_env="production",
        auth_mode="password",
        session_cookie_secure=True,
    )
    assert settings.auth_mode == "password"
