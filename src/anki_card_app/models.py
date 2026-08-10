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
    SKELETON_RECALL = "skeleton_recall"


class CardState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    REJECTED = "rejected"


class GenerationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ChunkGenerationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


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
generation_status_enum = Enum(
    GenerationStatus,
    native_enum=False,
    values_callable=lambda members: [member.value for member in members],
    length=16,
)
chunk_generation_status_enum = Enum(
    ChunkGenerationStatus,
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


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    prompt_version: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[GenerationStatus] = mapped_column(
        generation_status_enum, default=GenerationStatus.PENDING, index=True
    )
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    generated_cards: Mapped[int] = mapped_column(Integer, default=0)
    failed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("user_id", "content_fingerprint", name="uq_cards_user_fingerprint"),
        CheckConstraint(
            "card_type IN ('normal', 'cloze', 'skeleton_recall')",
            name="ck_cards_card_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL"), index=True
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_chunks.id", ondelete="SET NULL"), index=True
    )
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="SET NULL"), index=True
    )
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
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
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    ai_enrichment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(32), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class GenerationChunkRun(Base):
    __tablename__ = "generation_chunk_runs"
    __table_args__ = (
        UniqueConstraint(
            "generation_run_id", "source_chunk_id", name="uq_generation_chunk_runs_chunk"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_generation_chunk_attempts_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    generation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"), index=True
    )
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_chunks.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[ChunkGenerationStatus] = mapped_column(
        chunk_generation_status_enum, default=ChunkGenerationStatus.PENDING, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_count: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchedulingState(Base):
    __tablename__ = "scheduling_states"
    __table_args__ = (
        CheckConstraint("review_count >= 0", name="ck_scheduling_states_review_count_nonnegative"),
    )

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
    fsrs_card: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    __table_args__ = (
        CheckConstraint("queue_size >= 0", name="ck_review_sessions_queue_size_nonnegative"),
        CheckConstraint(
            "reviewed_count >= 0 AND reviewed_count <= queue_size",
            name="ck_review_sessions_reviewed_count_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    queue_size: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewSessionCard(Base):
    __tablename__ = "review_session_cards"
    __table_args__ = (
        UniqueConstraint("review_session_id", "card_id", name="uq_review_session_cards_card"),
        UniqueConstraint("review_session_id", "position", name="uq_review_session_cards_position"),
        CheckConstraint("position >= 0", name="ck_review_session_cards_position_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    review_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_sessions.id", ondelete="CASCADE"), index=True
    )
    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewLog(Base):
    __tablename__ = "review_logs"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 4", name="ck_review_logs_rating_range"),
        CheckConstraint(
            "response_time_ms IS NULL OR response_time_ms >= 0",
            name="ck_review_logs_response_time_nonnegative",
        ),
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
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    was_new: Mapped[bool] = mapped_column(default=False)
    elapsed_days: Mapped[float | None] = mapped_column(Float)
    prior_state: Mapped[dict[str, Any]] = mapped_column(JSON)
    new_state: Mapped[dict[str, Any]] = mapped_column(JSON)
