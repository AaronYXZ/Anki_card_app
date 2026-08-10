from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.fsrs_adapter import create_initial_schedule
from anki_card_app.models import (
    Card,
    CardState,
    CardType,
    CardVersion,
    UserAccount,
    utc_now,
)

CLOZE_PATTERN = re.compile(r"{{c[1-9]\d*::.+?}}", re.DOTALL)


class CardError(ValueError):
    """Base error for card operations."""


class CardValidationError(CardError):
    """Card content is incomplete or malformed."""


class CardNotFoundError(CardError):
    """The requested card does not belong to the current user."""


class InvalidCardTransitionError(CardError):
    """The requested lifecycle transition is not allowed."""


@dataclass(frozen=True, slots=True)
class CardContent:
    front: str | None = None
    back: str | None = None
    cloze_text: str | None = None
    back_extra: str | None = None


def content_fingerprint(card_type: CardType, content: CardContent) -> str:
    normalized = validate_content(card_type, content)
    parts = (
        card_type.value,
        normalized.front or "",
        normalized.back or "",
        normalized.cloze_text or "",
        normalized.back_extra or "",
    )
    canonical = "\x1f".join(" ".join(part.split()).casefold() for part in parts)
    return hashlib.sha256(canonical.encode()).hexdigest()


def clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def validate_content(card_type: CardType, content: CardContent) -> CardContent:
    normalized = CardContent(
        front=clean_optional(content.front),
        back=clean_optional(content.back),
        cloze_text=clean_optional(content.cloze_text),
        back_extra=clean_optional(content.back_extra),
    )

    if card_type is CardType.NORMAL:
        if normalized.front is None or normalized.back is None:
            raise CardValidationError("Normal cards require both a question and an answer.")
        return normalized

    if normalized.cloze_text is None:
        raise CardValidationError("Cloze cards require cloze text.")
    if CLOZE_PATTERN.search(normalized.cloze_text) is None:
        raise CardValidationError("Cloze text must include a deletion such as {{c1::answer}}.")
    return normalized


def get_owned_card(session: Session, *, user_id: uuid.UUID, card_id: uuid.UUID) -> Card:
    card = session.scalar(select(Card).where(Card.id == card_id, Card.user_id == user_id))
    if card is None:
        raise CardNotFoundError("Card not found.")
    return card


def get_current_version(session: Session, card: Card) -> CardVersion:
    if card.current_version_id is None:
        raise CardValidationError("Card has no current version.")
    version = session.get(CardVersion, card.current_version_id)
    if version is None:
        raise CardValidationError("Current card version is missing.")
    return version


def create_draft(
    session: Session,
    *,
    user_id: uuid.UUID,
    card_type: CardType,
    content: CardContent,
    created_by: str = "user",
    source_document_id: uuid.UUID | None = None,
    source_chunk_id: uuid.UUID | None = None,
    generation_run_id: uuid.UUID | None = None,
    source_excerpt: str | None = None,
    ai_enrichment: str | None = None,
) -> Card:
    normalized = validate_content(card_type, content)
    fingerprint = content_fingerprint(card_type, normalized)
    if session.scalar(
        select(Card.id).where(
            Card.user_id == user_id,
            Card.content_fingerprint == fingerprint,
        )
    ):
        raise CardValidationError("An exact duplicate card already exists.")
    card = Card(
        user_id=user_id,
        card_type=card_type,
        state=CardState.DRAFT,
        source_document_id=source_document_id,
        source_chunk_id=source_chunk_id,
        generation_run_id=generation_run_id,
        content_fingerprint=fingerprint,
    )
    session.add(card)
    session.flush()

    version = CardVersion(
        card_id=card.id,
        version_number=1,
        front=normalized.front,
        back=normalized.back,
        cloze_text=normalized.cloze_text,
        back_extra=normalized.back_extra,
        source_excerpt=clean_optional(source_excerpt),
        ai_enrichment=clean_optional(ai_enrichment),
        created_by=created_by,
    )
    session.add(version)
    session.flush()
    card.current_version_id = version.id
    session.flush()
    return card


def edit_card(
    session: Session,
    *,
    user_id: uuid.UUID,
    card_id: uuid.UUID,
    content: CardContent,
    created_by: str = "user",
) -> CardVersion:
    card = get_owned_card(session, user_id=user_id, card_id=card_id)
    if card.state not in {CardState.DRAFT, CardState.ACTIVE, CardState.SUSPENDED}:
        raise InvalidCardTransitionError(f"Cannot edit a {card.state.value} card.")

    normalized = validate_content(card.card_type, content)
    current_version = get_current_version(session, card)
    fingerprint = content_fingerprint(card.card_type, normalized)
    if session.scalar(
        select(Card.id).where(
            Card.user_id == user_id,
            Card.id != card.id,
            Card.content_fingerprint == fingerprint,
        )
    ):
        raise CardValidationError("An exact duplicate card already exists.")
    latest_version = session.scalar(
        select(func.max(CardVersion.version_number)).where(CardVersion.card_id == card.id)
    )
    version = CardVersion(
        card_id=card.id,
        version_number=(latest_version or 0) + 1,
        front=normalized.front,
        back=normalized.back,
        cloze_text=normalized.cloze_text,
        back_extra=normalized.back_extra,
        source_excerpt=current_version.source_excerpt,
        ai_enrichment=current_version.ai_enrichment,
        created_by=created_by,
    )
    session.add(version)
    session.flush()
    card.current_version_id = version.id
    card.content_fingerprint = fingerprint
    card.updated_at = utc_now()
    session.flush()
    return version


def approve_card(
    session: Session,
    *,
    user_id: uuid.UUID,
    card_id: uuid.UUID,
    due_at: datetime | None = None,
) -> Card:
    card = get_owned_card(session, user_id=user_id, card_id=card_id)
    if card.state is not CardState.DRAFT:
        raise InvalidCardTransitionError("Only draft cards can be approved.")

    version = get_current_version(session, card)
    validate_content(
        card.card_type,
        CardContent(
            front=version.front,
            back=version.back,
            cloze_text=version.cloze_text,
            back_extra=version.back_extra,
        ),
    )
    card.state = CardState.ACTIVE
    card.updated_at = utc_now()
    user = session.get(UserAccount, user_id)
    if user is None:
        raise CardValidationError("Card owner is missing.")
    session.add(
        create_initial_schedule(
            card_id=card.id,
            due_at=due_at or utc_now(),
            desired_retention=user.desired_retention,
        )
    )
    session.flush()
    return card


def reject_card(session: Session, *, user_id: uuid.UUID, card_id: uuid.UUID) -> Card:
    card = get_owned_card(session, user_id=user_id, card_id=card_id)
    if card.state is not CardState.DRAFT:
        raise InvalidCardTransitionError("Only draft cards can be rejected.")
    card.state = CardState.REJECTED
    card.updated_at = utc_now()
    session.flush()
    return card


def suspend_card(session: Session, *, user_id: uuid.UUID, card_id: uuid.UUID) -> Card:
    card = get_owned_card(session, user_id=user_id, card_id=card_id)
    if card.state is not CardState.ACTIVE:
        raise InvalidCardTransitionError("Only active cards can be suspended.")
    card.state = CardState.SUSPENDED
    card.updated_at = utc_now()
    session.flush()
    return card


def resume_card(session: Session, *, user_id: uuid.UUID, card_id: uuid.UUID) -> Card:
    card = get_owned_card(session, user_id=user_id, card_id=card_id)
    if card.state is not CardState.SUSPENDED:
        raise InvalidCardTransitionError("Only suspended cards can be resumed.")
    card.state = CardState.ACTIVE
    card.updated_at = utc_now()
    session.flush()
    return card


def retire_card(session: Session, *, user_id: uuid.UUID, card_id: uuid.UUID) -> Card:
    card = get_owned_card(session, user_id=user_id, card_id=card_id)
    if card.state not in {CardState.ACTIVE, CardState.SUSPENDED}:
        raise InvalidCardTransitionError("Only active or suspended cards can be retired.")
    card.state = CardState.RETIRED
    card.updated_at = utc_now()
    session.flush()
    return card
