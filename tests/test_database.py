from pytest import MonkeyPatch, raises

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


def test_get_session_yields_a_bound_session(monkeypatch: MonkeyPatch) -> None:
    configure_sqlite(monkeypatch)
    session_iterator = database.get_session()

    session = next(session_iterator)

    assert session.bind is database.get_engine()
    with raises(StopIteration):
        next(session_iterator)

    database.get_engine().dispose()
    database.get_engine.cache_clear()
