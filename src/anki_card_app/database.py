from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session

from anki_card_app.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        normalize_database_url(get_settings().database_url),
        pool_pre_ping=True,
    )


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def database_is_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            result: object = connection.scalar(text("SELECT 1"))
            return result == 1
    except SQLAlchemyError:
        return False
