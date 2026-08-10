import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import anki_card_app.review_service as review_service
from anki_card_app.card_service import CardContent, approve_card, create_draft, suspend_card
from anki_card_app.models import (
    Card,
    CardType,
    ReviewLog,
    ReviewSession,
    ReviewSessionCard,
    SchedulingState,
    UserAccount,
)
from anki_card_app.review_service import (
    ReviewConflictError,
    ReviewNotFoundError,
    get_next_entry,
    get_or_create_daily_session,
    reveal_answer,
    session_rating_counts,
    submit_review,
)
from anki_card_app.user_service import ensure_user

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def make_user(db_session: Session, *, daily_limit: int = 25, timezone: str = "UTC") -> UserAccount:
    identifier = uuid.uuid4()
    user = ensure_user(
        db_session,
        user_id=identifier,
        email=f"{identifier}@example.com",
    )
    user.daily_limit = daily_limit
    user.timezone = timezone
    db_session.flush()
    return user


def make_active_card(
    db_session: Session,
    user: UserAccount,
    *,
    question: str,
    due_at: datetime = NOW,
) -> Card:
    card = create_draft(
        db_session,
        user_id=user.id,
        card_type=CardType.NORMAL,
        content=CardContent(front=question, back=f"Answer to {question}"),
    )
    approve_card(db_session, user_id=user.id, card_id=card.id, due_at=due_at)
    return card


def test_queue_orders_due_reviews_before_new_and_resumes(db_session: Session) -> None:
    user = make_user(db_session, daily_limit=2)
    new_card = make_active_card(db_session, user, question="New")
    reviewed_card = make_active_card(
        db_session, user, question="Reviewed", due_at=NOW - timedelta(days=2)
    )
    future_card = make_active_card(
        db_session, user, question="Future", due_at=NOW + timedelta(days=1)
    )
    suspended_card = make_active_card(db_session, user, question="Suspended")
    reviewed_state = db_session.get(SchedulingState, reviewed_card.id)
    assert reviewed_state is not None
    reviewed_state.review_count = 1
    reviewed_state.scheduler_state = "review"
    suspend_card(db_session, user_id=user.id, card_id=suspended_card.id)

    review_session = get_or_create_daily_session(db_session, user_id=user.id, now=NOW)
    assert review_session is not None
    items = db_session.scalars(
        select(ReviewSessionCard)
        .where(ReviewSessionCard.review_session_id == review_session.id)
        .order_by(ReviewSessionCard.position)
    ).all()
    assert [item.card_id for item in items] == [reviewed_card.id, new_card.id]
    assert future_card.id not in {item.card_id for item in items}
    assert get_or_create_daily_session(db_session, user_id=user.id, now=NOW) is review_session


def test_reveal_submit_and_idempotent_retry(db_session: Session) -> None:
    user = make_user(db_session)
    card = make_active_card(db_session, user, question="Power")
    review_session = get_or_create_daily_session(db_session, user_id=user.id, now=NOW)
    assert review_session is not None
    entry = get_next_entry(db_session, user_id=user.id, session_id=review_session.id)
    assert entry is not None
    attempt_id = uuid.uuid4()

    with pytest.raises(ReviewConflictError, match="Reveal"):
        submit_review(
            db_session,
            user_id=user.id,
            session_id=review_session.id,
            card_id=card.id,
            attempt_id=attempt_id,
            rating=3,
            reviewed_at=NOW,
        )

    reveal_answer(
        db_session,
        user_id=user.id,
        session_id=review_session.id,
        card_id=card.id,
        revealed_at=NOW,
    )
    reveal_answer(
        db_session,
        user_id=user.id,
        session_id=review_session.id,
        card_id=card.id,
        revealed_at=NOW + timedelta(seconds=1),
    )
    first = submit_review(
        db_session,
        user_id=user.id,
        session_id=review_session.id,
        card_id=card.id,
        attempt_id=attempt_id,
        rating=3,
        reviewed_at=NOW + timedelta(seconds=4),
        enable_fuzzing=False,
    )
    retry = submit_review(
        db_session,
        user_id=user.id,
        session_id=review_session.id,
        card_id=card.id,
        attempt_id=attempt_id,
        rating=3,
        reviewed_at=NOW + timedelta(seconds=8),
        enable_fuzzing=False,
    )

    state = db_session.get(SchedulingState, card.id)
    assert first.created is True
    assert first.session_completed is True
    assert retry.created is False
    assert retry.log.id == first.log.id
    assert state is not None
    assert state.review_count == 1
    assert state.due_at == NOW + timedelta(minutes=10, seconds=4)
    assert first.log.response_time_ms == 4_000
    assert first.log.was_new is True
    assert first.log.prior_state["review_count"] == 0
    assert first.log.new_state["review_count"] == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewLog)) == 1
    assert session_rating_counts(
        db_session,
        user_id=user.id,
        session_id=review_session.id,
    ) == {3: 1}
    with pytest.raises(ReviewConflictError, match="different rating"):
        submit_review(
            db_session,
            user_id=user.id,
            session_id=review_session.id,
            card_id=card.id,
            attempt_id=attempt_id,
            rating=4,
        )


def test_invalid_and_cross_user_review_actions(db_session: Session) -> None:
    owner = make_user(db_session)
    stranger = make_user(db_session)
    card = make_active_card(db_session, owner, question="Owned")
    review_session = get_or_create_daily_session(db_session, user_id=owner.id, now=NOW)
    assert review_session is not None

    with pytest.raises(ReviewNotFoundError):
        get_next_entry(db_session, user_id=stranger.id, session_id=review_session.id)
    with pytest.raises(ReviewNotFoundError):
        reveal_answer(
            db_session,
            user_id=owner.id,
            session_id=review_session.id,
            card_id=uuid.uuid4(),
        )
    with pytest.raises(ReviewConflictError, match="Rating"):
        submit_review(
            db_session,
            user_id=owner.id,
            session_id=review_session.id,
            card_id=card.id,
            attempt_id=uuid.uuid4(),
            rating=0,
        )

    reveal_answer(
        db_session,
        user_id=owner.id,
        session_id=review_session.id,
        card_id=card.id,
        revealed_at=NOW,
    )
    attempt_id = uuid.uuid4()
    submit_review(
        db_session,
        user_id=owner.id,
        session_id=review_session.id,
        card_id=card.id,
        attempt_id=attempt_id,
        rating=2,
        reviewed_at=NOW,
        enable_fuzzing=False,
    )
    with pytest.raises(ReviewConflictError, match="already used"):
        submit_review(
            db_session,
            user_id=stranger.id,
            session_id=review_session.id,
            card_id=card.id,
            attempt_id=attempt_id,
            rating=2,
        )
    with pytest.raises(ReviewConflictError, match="already been reviewed"):
        reveal_answer(
            db_session,
            user_id=owner.id,
            session_id=review_session.id,
            card_id=card.id,
        )


def test_daily_limit_timezone_and_empty_queue(db_session: Session) -> None:
    user = make_user(db_session, daily_limit=1, timezone="America/Los_Angeles")
    card = make_active_card(db_session, user, question="Daily limit")
    db_session.add(
        ReviewLog(
            attempt_id=uuid.uuid4(),
            user_id=user.id,
            card_id=card.id,
            rating=4,
            reviewed_at=NOW,
            was_new=False,
            prior_state={},
            new_state={},
        )
    )
    db_session.flush()
    assert get_or_create_daily_session(db_session, user_id=user.id, now=NOW) is None

    user.timezone = "Invalid/Timezone"
    assert get_or_create_daily_session(db_session, user_id=user.id, now=NOW) is None
    with pytest.raises(ReviewNotFoundError, match="User"):
        get_or_create_daily_session(db_session, user_id=uuid.uuid4(), now=NOW)


def test_queue_completion_when_no_active_item_and_transaction_rollback(
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    user = make_user(db_session)
    card = make_active_card(db_session, user, question="Rollback")
    review_session = get_or_create_daily_session(db_session, user_id=user.id, now=NOW)
    assert review_session is not None
    reveal_answer(
        db_session,
        user_id=user.id,
        session_id=review_session.id,
        card_id=card.id,
        revealed_at=NOW,
    )
    db_session.commit()
    original_due = db_session.get(SchedulingState, card.id).due_at  # type: ignore[union-attr]

    def fail_schedule(*args: object, **kwargs: object) -> None:
        raise RuntimeError("scheduler failure")

    monkeypatch.setattr(review_service, "apply_review", fail_schedule)
    with pytest.raises(RuntimeError, match="scheduler failure"):
        submit_review(
            db_session,
            user_id=user.id,
            session_id=review_session.id,
            card_id=card.id,
            attempt_id=uuid.uuid4(),
            rating=1,
            reviewed_at=NOW,
        )
    db_session.rollback()
    assert db_session.get(SchedulingState, card.id).due_at == original_due  # type: ignore[union-attr]
    assert db_session.scalar(select(func.count()).select_from(ReviewLog)) == 0

    fresh_session = db_session.get(ReviewSession, review_session.id)
    assert fresh_session is not None
    item = db_session.scalar(
        select(ReviewSessionCard).where(ReviewSessionCard.review_session_id == fresh_session.id)
    )
    assert item is not None
    item.completed_at = NOW
    assert (
        get_next_entry(
            db_session,
            user_id=user.id,
            session_id=fresh_session.id,
        )
        is None
    )
    assert fresh_session.completed_at is not None
