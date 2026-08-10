from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.models import (
    Card,
    CardState,
    CardType,
    CardVersion,
    SchedulingState,
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
) -> Card:
    normalized = validate_content(card_type, content)
    card = Card(user_id=user_id, card_type=card_type, state=CardState.DRAFT)
    session.add(card)
    session.flush()

    version = CardVersion(
        card_id=card.id,
        version_number=1,
        front=normalized.front,
        back=normalized.back,
        cloze_text=normalized.cloze_text,
        back_extra=normalized.back_extra,
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
        created_by=created_by,
    )
    session.add(version)
    session.flush()
    card.current_version_id = version.id
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
    session.add(
        SchedulingState(
            card_id=card.id,
            due_at=due_at or utc_now(),
            scheduler_state="new",
            algorithm="uninitialized",
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
