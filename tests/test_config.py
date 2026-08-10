from pytest import MonkeyPatch

from anki_card_app.config import Settings


def test_settings_accept_environment_overrides(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_DEBUG", "true")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.debug is True
