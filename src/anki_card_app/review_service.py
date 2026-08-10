from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.fsrs_adapter import apply_review, as_utc
from anki_card_app.models import (
    Card,
    CardState,
    CardVersion,
    ReviewLog,
    ReviewSession,
    ReviewSessionCard,
    SchedulingState,
    UserAccount,
    utc_now,
)


class ReviewError(ValueError):
    """Base error for review queue operations."""


class ReviewNotFoundError(ReviewError):
    """The requested review resource does not belong to the user."""


class ReviewConflictError(ReviewError):
    """The review action is invalid for the current queue state."""


@dataclass(frozen=True, slots=True)
class ReviewQueueEntry:
    session: ReviewSession
    item: ReviewSessionCard
    card: Card
    version: CardVersion
    scheduling: SchedulingState


@dataclass(frozen=True, slots=True)
class ReviewSubmission:
    log: ReviewLog
    created: bool
    session_completed: bool


def _owned_session(session: Session, *, user_id: uuid.UUID, session_id: uuid.UUID) -> ReviewSession:
    review_session = session.scalar(
        select(ReviewSession).where(
            ReviewSession.id == session_id,
            ReviewSession.user_id == user_id,
        )
    )
    if review_session is None:
        raise ReviewNotFoundError("Review session not found.")
    return review_session


def _day_start_utc(now: datetime, timezone_name: str) -> datetime:
    try:
        timezone: tzinfo = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = UTC
    local_now = as_utc(now).astimezone(timezone)
    return datetime.combine(local_now.date(), time.min, tzinfo=timezone).astimezone(UTC)


def get_or_create_daily_session(
    session: Session,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> ReviewSession | None:
    current_time = as_utc(now or utc_now())
    active_session = session.scalar(
        select(ReviewSession)
        .where(
            ReviewSession.user_id == user_id,
            ReviewSession.completed_at.is_(None),
        )
        .order_by(ReviewSession.started_at.desc())
        .limit(1)
    )
    if active_session is not None:
        return active_session

    user = session.get(UserAccount, user_id)
    if user is None:
        raise ReviewNotFoundError("User not found.")
    reviewed_today = (
        session.scalar(
            select(func.count())
            .select_from(ReviewLog)
            .where(
                ReviewLog.user_id == user_id,
                ReviewLog.reviewed_at >= _day_start_utc(current_time, user.timezone),
            )
        )
        or 0
    )
    remaining = max(0, user.daily_limit - reviewed_today)
    if remaining == 0:
        return None

    common_filters = (
        Card.user_id == user_id,
        Card.state == CardState.ACTIVE,
        SchedulingState.due_at <= current_time,
    )
    reviewed_ids = session.scalars(
        select(Card.id)
        .join(SchedulingState, SchedulingState.card_id == Card.id)
        .where(*common_filters, SchedulingState.review_count > 0)
        .order_by(SchedulingState.due_at, Card.created_at, Card.id)
        .limit(remaining)
    ).all()
    remaining -= len(reviewed_ids)
    new_ids = (
        session.scalars(
            select(Card.id)
            .join(SchedulingState, SchedulingState.card_id == Card.id)
            .where(*common_filters, SchedulingState.review_count == 0)
            .order_by(Card.created_at, Card.id)
            .limit(remaining)
        ).all()
        if remaining
        else []
    )
    card_ids = [*reviewed_ids, *new_ids]
    if not card_ids:
        return None

    review_session = ReviewSession(
        user_id=user_id,
        queue_size=len(card_ids),
        reviewed_count=0,
        started_at=current_time,
    )
    session.add(review_session)
    session.flush()
    session.add_all(
        ReviewSessionCard(
            review_session_id=review_session.id,
            card_id=card_id,
            position=position,
        )
        for position, card_id in enumerate(card_ids)
    )
    session.flush()
    return review_session


def get_next_entry(
    session: Session,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> ReviewQueueEntry | None:
    review_session = _owned_session(session, user_id=user_id, session_id=session_id)
    row = session.execute(
        select(ReviewSessionCard, Card, CardVersion, SchedulingState)
        .join(Card, Card.id == ReviewSessionCard.card_id)
        .join(CardVersion, CardVersion.id == Card.current_version_id)
        .join(SchedulingState, SchedulingState.card_id == Card.id)
        .where(
            ReviewSessionCard.review_session_id == review_session.id,
            ReviewSessionCard.completed_at.is_(None),
            Card.user_id == user_id,
            Card.state == CardState.ACTIVE,
        )
        .order_by(ReviewSessionCard.position)
        .limit(1)
    ).one_or_none()
    if row is None:
        if review_session.completed_at is None:
            review_session.completed_at = utc_now()
        return None
    item, card, version, scheduling = row
    return ReviewQueueEntry(review_session, item, card, version, scheduling)


def reveal_answer(
    session: Session,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    card_id: uuid.UUID,
    revealed_at: datetime | None = None,
) -> ReviewSessionCard:
    review_session = _owned_session(session, user_id=user_id, session_id=session_id)
    item = session.scalar(
        select(ReviewSessionCard).where(
            ReviewSessionCard.review_session_id == review_session.id,
            ReviewSessionCard.card_id == card_id,
        )
    )
    if item is None:
        raise ReviewNotFoundError("Card is not in this review session.")
    if item.completed_at is not None:
        raise ReviewConflictError("This card has already been reviewed.")
    item.revealed_at = item.revealed_at or as_utc(revealed_at or utc_now())
    session.flush()
    return item


def _existing_submission(
    session: Session,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    card_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> ReviewLog | None:
    existing = session.scalar(select(ReviewLog).where(ReviewLog.attempt_id == attempt_id))
    if existing is None:
        return None
    if (
        existing.user_id != user_id
        or existing.review_session_id != session_id
        or existing.card_id != card_id
    ):
        raise ReviewConflictError("Review attempt identifier was already used.")
    return existing


def submit_review(
    session: Session,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    card_id: uuid.UUID,
    attempt_id: uuid.UUID,
    rating: int,
    reviewed_at: datetime | None = None,
    enable_fuzzing: bool | None = None,
) -> ReviewSubmission:
    if rating not in {1, 2, 3, 4}:
        raise ReviewConflictError("Rating must be Again, Hard, Good, or Easy.")
    existing = _existing_submission(
        session,
        user_id=user_id,
        session_id=session_id,
        card_id=card_id,
        attempt_id=attempt_id,
    )
    if existing is not None:
        if existing.rating != rating:
            raise ReviewConflictError("Review attempt identifier has a different rating.")
        review_session = _owned_session(session, user_id=user_id, session_id=session_id)
        return ReviewSubmission(existing, False, review_session.completed_at is not None)

    review_session = _owned_session(session, user_id=user_id, session_id=session_id)
    item = session.scalar(
        select(ReviewSessionCard)
        .where(
            ReviewSessionCard.review_session_id == review_session.id,
            ReviewSessionCard.card_id == card_id,
        )
        .with_for_update()
    )
    if item is None:
        raise ReviewNotFoundError("Card is not in this review session.")
    existing = _existing_submission(
        session,
        user_id=user_id,
        session_id=session_id,
        card_id=card_id,
        attempt_id=attempt_id,
    )
    if existing is not None:
        if existing.rating != rating:
            raise ReviewConflictError("Review attempt identifier has a different rating.")
        return ReviewSubmission(existing, False, review_session.completed_at is not None)
    if item.completed_at is not None:
        raise ReviewConflictError("This card has already been reviewed.")
    if item.revealed_at is None:
        raise ReviewConflictError("Reveal the answer before rating this card.")

    current_time = as_utc(reviewed_at or utc_now())
    card = session.scalar(
        select(Card)
        .where(Card.id == card_id, Card.user_id == user_id, Card.state == CardState.ACTIVE)
        .with_for_update()
    )
    scheduling = session.scalar(
        select(SchedulingState).where(SchedulingState.card_id == card_id).with_for_update()
    )
    user = session.get(UserAccount, user_id)
    if card is None or scheduling is None or user is None:
        raise ReviewNotFoundError("Active card scheduling state not found.")

    revealed_at = as_utc(item.revealed_at)
    response_time_ms = max(0, int((current_time - revealed_at).total_seconds() * 1_000))
    was_new = scheduling.review_count == 0
    result = apply_review(
        scheduling,
        rating=rating,
        reviewed_at=current_time,
        desired_retention=user.desired_retention,
        review_duration_ms=response_time_ms,
        enable_fuzzing=enable_fuzzing,
    )
    review_log = ReviewLog(
        attempt_id=attempt_id,
        user_id=user_id,
        card_id=card_id,
        review_session_id=review_session.id,
        rating=rating,
        reviewed_at=current_time,
        response_time_ms=response_time_ms,
        was_new=was_new,
        elapsed_days=result.elapsed_days,
        prior_state=result.prior_state,
        new_state=result.new_state,
    )
    session.add(review_log)
    item.completed_at = current_time
    review_session.reviewed_count += 1
    if review_session.reviewed_count >= review_session.queue_size:
        review_session.completed_at = current_time
    session.flush()
    return ReviewSubmission(review_log, True, review_session.completed_at is not None)


def session_rating_counts(
    session: Session,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> dict[int, int]:
    _owned_session(session, user_id=user_id, session_id=session_id)
    rows = session.execute(
        select(ReviewLog.rating, func.count())
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.review_session_id == session_id,
        )
        .group_by(ReviewLog.rating)
    ).all()
    return {rating: count for rating, count in rows}
