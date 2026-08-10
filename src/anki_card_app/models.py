from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from anki_card_app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CardType(StrEnum):
    NORMAL = "normal"
    CLOZE = "cloze"


class CardState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    REJECTED = "rejected"


card_type_enum = Enum(
    CardType,
    native_enum=False,
    values_callable=lambda members: [member.value for member in members],
    length=16,
)
card_state_enum = Enum(
    CardState,
    native_enum=False,
    values_callable=lambda members: [member.value for member in members],
    length=16,
)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint("daily_limit > 0", name="ck_user_accounts_daily_limit_positive"),
        CheckConstraint(
            "desired_retention > 0 AND desired_retention <= 1",
            name="ck_user_accounts_desired_retention_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    daily_limit: Mapped[int] = mapped_column(Integer, default=25)
    desired_retention: Mapped[float] = mapped_column(Float, default=0.9)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", name="uq_source_documents_user_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    relative_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_content: Mapped[str] = mapped_column(Text)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceChunk(Base):
    __tablename__ = "source_chunks"
    __table_args__ = (
        UniqueConstraint("source_document_id", "sequence", name="uq_source_chunks_sequence"),
        CheckConstraint("sequence >= 0", name="ck_source_chunks_sequence_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL"), index=True
    )
    card_type: Mapped[CardType] = mapped_column(card_type_enum)
    state: Mapped[CardState] = mapped_column(card_state_enum, default=CardState.DRAFT, index=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "card_versions.id",
            name="fk_cards_current_version",
            use_alter=True,
            ondelete="RESTRICT",
        )
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CardVersion(Base):
    __tablename__ = "card_versions"
    __table_args__ = (
        UniqueConstraint("card_id", "version_number", name="uq_card_versions_number"),
        CheckConstraint("version_number > 0", name="ck_card_versions_number_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    front: Mapped[str | None] = mapped_column(Text)
    back: Mapped[str | None] = mapped_column(Text)
    cloze_text: Mapped[str | None] = mapped_column(Text)
    back_extra: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(32), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SchedulingState(Base):
    __tablename__ = "scheduling_states"

    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    scheduler_state: Mapped[str] = mapped_column(String(32), default="new")
    step: Mapped[int | None] = mapped_column(Integer)
    algorithm: Mapped[str] = mapped_column(String(32), default="uninitialized")
    algorithm_version: Mapped[str | None] = mapped_column(String(32))
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ReviewSession(Base):
    __tablename__ = "review_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    queue_size: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewLog(Base):
    __tablename__ = "review_logs"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 4", name="ck_review_logs_rating_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    review_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_sessions.id", ondelete="SET NULL"), index=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    elapsed_days: Mapped[float | None] = mapped_column(Float)
    prior_state: Mapped[dict[str, Any]] = mapped_column(JSON)
    new_state: Mapped[dict[str, Any]] = mapped_column(JSON)
