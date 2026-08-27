from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.card_service import CardContent, approve_card, create_draft
from anki_card_app.export_service import build_user_export
from anki_card_app.models import (
    Card,
    CardState,
    CardType,
    CardVersion,
    ChunkGenerationStatus,
    GenerationChunkRun,
    GenerationRun,
    GenerationStatus,
    ReviewLog,
    SourceChunk,
    SourceDocument,
    UserAccount,
    utc_now,
)
from anki_card_app.restore_service import (
    RestoreValidationError,
    parse_backup_json,
    restore_user_export,
)
from anki_card_app.review_service import (
    get_or_create_daily_session,
    reveal_answer,
    submit_review,
)


def _complete_export(session: Session, *, user: UserAccount) -> dict[str, Any]:
    user.timezone = "America/Los_Angeles"
    user.daily_limit = 41
    user.desired_retention = 0.87
    source = SourceDocument(
        user_id=user.id,
        relative_path="ML/attention.md",
        filename="attention.md",
        content_hash="a" * 64,
        raw_content="# Attention\nQueries attend to keys and retrieve values.",
    )
    session.add(source)
    session.flush()
    chunk = SourceChunk(
        source_document_id=source.id,
        sequence=0,
        heading_path="Attention",
        text="Queries attend to keys and retrieve values.",
        token_estimate=9,
    )
    session.add(chunk)
    session.flush()
    run = GenerationRun(
        user_id=user.id,
        source_document_id=source.id,
        prompt_version="v1",
        provider="openai",
        model="gpt-5.6-terra",
        input_hash=source.content_hash,
        status=GenerationStatus.COMPLETED,
        total_chunks=1,
        completed_chunks=1,
        generated_cards=2,
        failed_chunks=0,
        completed_at=utc_now(),
    )
    session.add(run)
    session.flush()
    session.add(
        GenerationChunkRun(
            generation_run_id=run.id,
            source_chunk_id=chunk.id,
            status=ChunkGenerationStatus.COMPLETED,
            attempt_count=1,
            generated_count=2,
            request_id="request-123",
            completed_at=utc_now(),
        )
    )
    active = create_draft(
        session,
        user_id=user.id,
        card_type=CardType.NORMAL,
        content=CardContent(front="What does attention retrieve?", back="Values."),
        created_by="ai",
        source_document_id=source.id,
        source_chunk_id=chunk.id,
        generation_run_id=run.id,
        source_excerpt=chunk.text,
    )
    active.is_favorite = True
    approve_card(session, user_id=user.id, card_id=active.id, due_at=utc_now())
    create_draft(
        session,
        user_id=user.id,
        card_type=CardType.CLOZE,
        content=CardContent(cloze_text="Queries attend to {{c1::keys}}."),
        created_by="ai",
        source_document_id=source.id,
        source_chunk_id=chunk.id,
        generation_run_id=run.id,
    )
    review_session = get_or_create_daily_session(session, user_id=user.id, now=utc_now())
    assert review_session is not None
    reveal_answer(
        session,
        user_id=user.id,
        session_id=review_session.id,
        card_id=active.id,
        revealed_at=utc_now(),
    )
    submit_review(
        session,
        user_id=user.id,
        session_id=review_session.id,
        card_id=active.id,
        attempt_id=uuid.uuid4(),
        rating=3,
        reviewed_at=utc_now(),
        enable_fuzzing=False,
    )
    session.commit()
    return build_user_export(session, user_id=user.id)


def test_restore_round_trip_preserves_learning_history_and_drafts(db_session: Session) -> None:
    source_user = UserAccount(email="source@example.com", password_hash="source-password-hash")
    target_user = UserAccount(email="target@example.com", password_hash="keep-target-password-hash")
    db_session.add_all([source_user, target_user])
    db_session.flush()
    payload = _complete_export(db_session, user=source_user)
    source_card_ids = {
        card.id for card in db_session.scalars(select(Card).where(Card.user_id == source_user.id))
    }

    result = restore_user_export(db_session, user_id=target_user.id, payload=payload)
    db_session.commit()

    restored_cards = db_session.scalars(
        select(Card).where(Card.user_id == target_user.id).order_by(Card.state)
    ).all()
    restored_versions = db_session.scalars(
        select(CardVersion)
        .join(Card, Card.id == CardVersion.card_id)
        .where(Card.user_id == target_user.id)
    ).all()
    restored_logs = db_session.scalars(
        select(ReviewLog).where(ReviewLog.user_id == target_user.id)
    ).all()
    db_session.refresh(target_user)
    assert result.counts["cards"] == 2
    assert result.counts["card_versions"] == 2
    assert result.counts["review_logs"] == 1
    assert result.total_rows == sum(len(rows) for rows in payload["data"].values())
    assert {card.state for card in restored_cards} == {CardState.ACTIVE, CardState.DRAFT}
    assert sum(card.is_favorite for card in restored_cards) == 1
    assert not source_card_ids.intersection(card.id for card in restored_cards)
    assert {version.created_by for version in restored_versions} == {"ai"}
    assert restored_logs[0].rating == 3
    assert restored_logs[0].prior_state
    assert restored_logs[0].new_state
    assert target_user.email == "target@example.com"
    assert target_user.password_hash == "keep-target-password-hash"
    assert target_user.timezone == "America/Los_Angeles"
    assert target_user.daily_limit == 41
    assert target_user.desired_retention == pytest.approx(0.87)


def test_restore_old_backup_defaults_missing_favorites_to_false(db_session: Session) -> None:
    source_user = UserAccount(email="legacy-source@example.com")
    target_user = UserAccount(email="legacy-target@example.com")
    db_session.add_all([source_user, target_user])
    db_session.flush()
    payload = _complete_export(db_session, user=source_user)
    for card in payload["data"]["cards"]:
        del card["is_favorite"]

    restore_user_export(db_session, user_id=target_user.id, payload=payload)
    db_session.commit()

    restored_cards = db_session.scalars(select(Card).where(Card.user_id == target_user.id)).all()
    assert restored_cards
    assert all(card.is_favorite is False for card in restored_cards)


def test_restore_rejects_nonempty_account_without_changing_it(db_session: Session) -> None:
    source_user = UserAccount(email="source@example.com")
    target_user = UserAccount(email="target@example.com")
    db_session.add_all([source_user, target_user])
    db_session.flush()
    payload = _complete_export(db_session, user=source_user)
    existing = create_draft(
        db_session,
        user_id=target_user.id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Existing", back="Keep me"),
    )
    db_session.commit()

    with pytest.raises(RestoreValidationError, match="empty account"):
        restore_user_export(db_session, user_id=target_user.id, payload=payload)

    assert (
        db_session.scalar(
            select(func.count()).select_from(Card).where(Card.user_id == target_user.id)
        )
        == 1
    )
    assert db_session.get(Card, existing.id) is not None


def test_restore_rejects_broken_relationship_before_writing(db_session: Session) -> None:
    source_user = UserAccount(email="source@example.com")
    target_user = UserAccount(email="target@example.com")
    db_session.add_all([source_user, target_user])
    db_session.flush()
    payload = _complete_export(db_session, user=source_user)
    payload["data"]["cards"][0]["source_document_id"] = str(uuid.uuid4())

    with pytest.raises(RestoreValidationError, match="references a missing row"):
        restore_user_export(db_session, user_id=target_user.id, payload=payload)

    assert (
        db_session.scalar(
            select(func.count()).select_from(Card).where(Card.user_id == target_user.id)
        )
        == 0
    )


def test_backup_json_parser_rejects_invalid_and_ambiguous_json() -> None:
    with pytest.raises(RestoreValidationError, match="invalid JSON"):
        parse_backup_json(b"{not-json}")
    with pytest.raises(RestoreValidationError, match="duplicate JSON key"):
        parse_backup_json(b'{"format": "one", "format": "two"}')
    with pytest.raises(RestoreValidationError, match="non-finite"):
        parse_backup_json(b'{"value": NaN}')


def test_restore_rejects_wrong_format_version(db_session: Session) -> None:
    target_user = UserAccount(email="target@example.com")
    db_session.add(target_user)
    db_session.flush()
    payload = {
        "format": "anki-card-app-backup",
        "format_version": 999,
        "user": {},
        "data": {},
    }

    with pytest.raises(RestoreValidationError, match="Unsupported backup format version"):
        restore_user_export(db_session, user_id=target_user.id, payload=payload)


def test_restore_rejects_current_version_from_another_card(db_session: Session) -> None:
    source_user = UserAccount(email="source@example.com")
    target_user = UserAccount(email="target@example.com")
    db_session.add_all([source_user, target_user])
    db_session.flush()
    payload = _complete_export(db_session, user=source_user)
    cards = payload["data"]["cards"]
    cards[0]["current_version_id"] = cards[1]["current_version_id"]

    with pytest.raises(RestoreValidationError, match="does not belong"):
        restore_user_export(db_session, user_id=target_user.id, payload=payload)
