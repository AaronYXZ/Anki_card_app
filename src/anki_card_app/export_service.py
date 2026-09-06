from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from anki_card_app.models import (
    Card,
    CardVersion,
    GenerationChunkRun,
    GenerationRun,
    ReviewLog,
    ReviewSession,
    ReviewSessionCard,
    SchedulingState,
    SourceChunk,
    SourceDocument,
    StudyNote,
    UserAccount,
    utc_now,
)

EXPORT_FORMAT_VERSION = 2


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value


def _serialize_row(row: object) -> dict[str, Any]:
    inspection = inspect(row)
    if inspection is None:
        raise TypeError("Expected a mapped database row.")
    mapper = inspection.mapper
    return {
        attribute.key: _serialize_value(getattr(row, attribute.key))
        for attribute in mapper.column_attrs
    }


def build_user_export(session: Session, *, user_id: uuid.UUID) -> dict[str, Any]:
    user = session.get(UserAccount, user_id)
    if user is None:
        raise LookupError("User not found.")

    source_documents = session.scalars(
        select(SourceDocument)
        .where(SourceDocument.user_id == user_id)
        .order_by(SourceDocument.imported_at, SourceDocument.id)
    ).all()
    source_chunks = session.scalars(
        select(SourceChunk)
        .join(SourceDocument, SourceDocument.id == SourceChunk.source_document_id)
        .where(SourceDocument.user_id == user_id)
        .order_by(SourceChunk.source_document_id, SourceChunk.sequence)
    ).all()
    generation_runs = session.scalars(
        select(GenerationRun)
        .where(GenerationRun.user_id == user_id)
        .order_by(GenerationRun.created_at, GenerationRun.id)
    ).all()
    generation_chunk_runs = session.scalars(
        select(GenerationChunkRun)
        .join(GenerationRun, GenerationRun.id == GenerationChunkRun.generation_run_id)
        .where(GenerationRun.user_id == user_id)
        .order_by(GenerationChunkRun.generation_run_id, GenerationChunkRun.id)
    ).all()
    study_notes = session.scalars(
        select(StudyNote)
        .where(StudyNote.user_id == user_id)
        .order_by(StudyNote.created_at, StudyNote.id)
    ).all()
    cards = session.scalars(
        select(Card).where(Card.user_id == user_id).order_by(Card.created_at, Card.id)
    ).all()
    card_versions = session.scalars(
        select(CardVersion)
        .join(Card, Card.id == CardVersion.card_id)
        .where(Card.user_id == user_id)
        .order_by(CardVersion.card_id, CardVersion.version_number)
    ).all()
    scheduling_states = session.scalars(
        select(SchedulingState)
        .join(Card, Card.id == SchedulingState.card_id)
        .where(Card.user_id == user_id)
        .order_by(SchedulingState.card_id)
    ).all()
    review_sessions = session.scalars(
        select(ReviewSession)
        .where(ReviewSession.user_id == user_id)
        .order_by(ReviewSession.started_at, ReviewSession.id)
    ).all()
    review_session_cards = session.scalars(
        select(ReviewSessionCard)
        .join(ReviewSession, ReviewSession.id == ReviewSessionCard.review_session_id)
        .where(ReviewSession.user_id == user_id)
        .order_by(ReviewSessionCard.review_session_id, ReviewSessionCard.position)
    ).all()
    review_logs = session.scalars(
        select(ReviewLog)
        .where(ReviewLog.user_id == user_id)
        .order_by(ReviewLog.reviewed_at, ReviewLog.id)
    ).all()

    return {
        "format": "anki-card-app-backup",
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": utc_now().isoformat(),
        "user": {
            "id": str(user.id),
            "email": user.email,
            "timezone": user.timezone,
            "daily_limit": user.daily_limit,
            "desired_retention": user.desired_retention,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
        },
        "data": {
            "source_documents": [_serialize_row(row) for row in source_documents],
            "source_chunks": [_serialize_row(row) for row in source_chunks],
            "generation_runs": [_serialize_row(row) for row in generation_runs],
            "generation_chunk_runs": [_serialize_row(row) for row in generation_chunk_runs],
            "study_notes": [_serialize_row(row) for row in study_notes],
            "cards": [_serialize_row(row) for row in cards],
            "card_versions": [_serialize_row(row) for row in card_versions],
            "scheduling_states": [_serialize_row(row) for row in scheduling_states],
            "review_sessions": [_serialize_row(row) for row in review_sessions],
            "review_session_cards": [_serialize_row(row) for row in review_session_cards],
            "review_logs": [_serialize_row(row) for row in review_logs],
        },
    }
