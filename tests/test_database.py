from pytest import MonkeyPatch, raises
from sqlalchemy import create_engine

import anki_card_app.database as database
from anki_card_app.config import Settings


def configure_sqlite(monkeypatch: MonkeyPatch) -> None:
    settings = Settings(database_url="sqlite://")
    monkeypatch.setattr(database, "get_settings", lambda: settings)
    database.get_engine.cache_clear()


def test_get_engine_uses_configured_database(monkeypatch: MonkeyPatch) -> None:
    configure_sqlite(monkeypatch)

    engine = database.get_engine()

    assert str(engine.url) == "sqlite://"
    engine.dispose()
    database.get_engine.cache_clear()


def test_railway_postgres_urls_select_psycopg_driver() -> None:
    assert database.normalize_database_url("postgres://user:pass@db/app") == (
        "postgresql+psycopg://user:pass@db/app"
    )
    assert database.normalize_database_url("postgresql://user:pass@db/app") == (
        "postgresql+psycopg://user:pass@db/app"
    )
    assert database.normalize_database_url("postgresql+psycopg://user:pass@db/app") == (
        "postgresql+psycopg://user:pass@db/app"
    )


def test_get_session_yields_a_bound_session(monkeypatch: MonkeyPatch) -> None:
    configure_sqlite(monkeypatch)
    session_iterator = database.get_session()

    session = next(session_iterator)

    assert session.bind is database.get_engine()
    with raises(StopIteration):
        next(session_iterator)

    database.get_engine().dispose()
    database.get_engine.cache_clear()


def test_database_readiness_handles_success_and_failure(monkeypatch: MonkeyPatch) -> None:
    ready_engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "get_engine", lambda: ready_engine)
    assert database.database_is_ready()
    ready_engine.dispose()

    failed_engine = create_engine("sqlite:////directory-that-does-not-exist/database.db")
    monkeypatch.setattr(database, "get_engine", lambda: failed_engine)
    assert not database.database_is_ready()
    failed_engine.dispose()
