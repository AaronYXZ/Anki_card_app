from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import anki_card_app.imports_web as imports_web
from anki_card_app.app import create_app
from anki_card_app.config import get_settings
from anki_card_app.database import Base, get_session


@pytest.fixture
def test_engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    with Session(test_engine, expire_on_commit=False) as session:
        yield session
        session.rollback()


@pytest.fixture
def test_app(db_session: Session, monkeypatch: MonkeyPatch) -> FastAPI:
    application = create_app()
    configured = get_settings()
    import_settings = SimpleNamespace(
        development_user_id=configured.development_user_id,
        development_user_email=configured.development_user_email,
        max_upload_bytes=configured.max_upload_bytes,
        max_archive_files=configured.max_archive_files,
        max_archive_uncompressed_bytes=configured.max_archive_uncompressed_bytes,
        openai_api_key=None,
        openai_model=configured.openai_model,
        openai_timeout_seconds=configured.openai_timeout_seconds,
        openai_max_retries=configured.openai_max_retries,
    )
    monkeypatch.setattr(imports_web, "get_settings", lambda: import_settings)

    def override_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_session
    return application


@pytest.fixture
def client(test_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(test_app) as test_client:
        yield test_client
