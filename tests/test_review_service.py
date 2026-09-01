import uuid
from collections import Counter
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


def test_pacific_day_start_tracks_daylight_saving_time() -> None:
    summer = datetime(2026, 8, 25, 20, tzinfo=UTC)
    winter = datetime(2026, 12, 25, 20, tzinfo=UTC)

    assert review_service._day_start_utc(summer, "America/Los_Angeles") == datetime(
        2026, 8, 25, 7, tzinfo=UTC
    )
    assert review_service._day_start_utc(winter, "America/Los_Angeles") == datetime(
        2026, 12, 25, 8, tzinfo=UTC
    )


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
    card_type: CardType = CardType.NORMAL,
) -> Card:
    content = (
        CardContent(cloze_text=f"{question} is {{{{c1::remembered}}}}.")
        if card_type is CardType.CLOZE
        else CardContent(front=question, back=f"Answer to {question}")
    )
    card = create_draft(
        db_session,
        user_id=user.id,
        card_type=card_type,
        content=content,
    )
    approve_card(db_session, user_id=user.id, card_id=card.id, due_at=due_at)
    return card


def queued_card_types(db_session: Session, review_session: ReviewSession) -> Counter[CardType]:
    return Counter(
        db_session.scalars(
            select(Card.card_type)
            .join(ReviewSessionCard, ReviewSessionCard.card_id == Card.id)
            .where(ReviewSessionCard.review_session_id == review_session.id)
        ).all()
    )


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


def test_queue_reserves_normal_and_skeleton_daily_minimums(db_session: Session) -> None:
    user = make_user(db_session, daily_limit=15)
    for number in range(10):
        make_active_card(db_session, user, question=f"Normal {number}")
    for number in range(3):
        make_active_card(
            db_session,
            user,
            question=f"Skeleton {number}",
            card_type=CardType.SKELETON_RECALL,
        )
    reviewed_cloze_ids = []
    for number in range(5):
        card = make_active_card(
            db_session,
            user,
            question=f"Cloze {number}",
            due_at=NOW - timedelta(days=2),
            card_type=CardType.CLOZE,
        )
        reviewed_cloze_ids.append(card.id)
        state = db_session.get(SchedulingState, card.id)
        assert state is not None
        state.review_count = 1
        state.scheduler_state = "review"

    review_session = get_or_create_daily_session(db_session, user_id=user.id, now=NOW)

    assert review_session is not None
    assert review_session.queue_size == 15
    assert queued_card_types(db_session, review_session) == Counter(
        {
            CardType.NORMAL: 10,
            CardType.SKELETON_RECALL: 3,
            CardType.CLOZE: 2,
        }
    )
    queued_ids = db_session.scalars(
        select(ReviewSessionCard.card_id)
        .where(ReviewSessionCard.review_session_id == review_session.id)
        .order_by(ReviewSessionCard.position)
    ).all()
    assert set(queued_ids[:2]).issubset(set(reviewed_cloze_ids))


def test_queue_fills_other_types_when_quota_cards_are_unavailable(db_session: Session) -> None:
    user = make_user(db_session, daily_limit=15)
    for number in range(4):
        make_active_card(db_session, user, question=f"Normal {number}")
    make_active_card(
        db_session,
        user,
        question="Only skeleton",
        card_type=CardType.SKELETON_RECALL,
    )
    for number in range(20):
        make_active_card(
            db_session,
            user,
            question=f"Cloze {number}",
            card_type=CardType.CLOZE,
        )

    review_session = get_or_create_daily_session(db_session, user_id=user.id, now=NOW)

    assert review_session is not None
    assert queued_card_types(db_session, review_session) == Counter(
        {
            CardType.CLOZE: 10,
            CardType.NORMAL: 4,
            CardType.SKELETON_RECALL: 1,
        }
    )


def test_queue_counts_reviews_already_completed_toward_type_minimums(
    db_session: Session,
) -> None:
    user = make_user(db_session, daily_limit=13)
    completed_cards = [
        *(
            make_active_card(
                db_session,
                user,
                question=f"Completed normal {number}",
                due_at=NOW + timedelta(days=1),
            )
            for number in range(8)
        ),
        *(
            make_active_card(
                db_session,
                user,
                question=f"Completed skeleton {number}",
                due_at=NOW + timedelta(days=1),
                card_type=CardType.SKELETON_RECALL,
            )
            for number in range(2)
        ),
    ]
    db_session.add_all(
        ReviewLog(
            attempt_id=uuid.uuid4(),
            user_id=user.id,
            card_id=card.id,
            rating=3,
            reviewed_at=NOW,
            was_new=False,
            prior_state={},
            new_state={},
        )
        for card in completed_cards
    )
    for number in range(10):
        make_active_card(db_session, user, question=f"Due normal {number}")
    for number in range(3):
        make_active_card(
            db_session,
            user,
            question=f"Due skeleton {number}",
            card_type=CardType.SKELETON_RECALL,
        )
    for number in range(5):
        make_active_card(
            db_session,
            user,
            question=f"Due cloze {number}",
            card_type=CardType.CLOZE,
        )

    review_session = get_or_create_daily_session(db_session, user_id=user.id, now=NOW)

    assert review_session is not None
    assert review_session.queue_size == 3
    assert queued_card_types(db_session, review_session) == Counter(
        {CardType.NORMAL: 2, CardType.SKELETON_RECALL: 1}
    )


def test_queue_closes_stale_session_and_rebuilds_daily_type_minimums(
    db_session: Session,
) -> None:
    user = make_user(db_session, daily_limit=13)
    stale_card = make_active_card(
        db_session,
        user,
        question="Stale cloze",
        card_type=CardType.CLOZE,
    )
    stale_session = ReviewSession(
        user_id=user.id,
        queue_size=1,
        reviewed_count=0,
        started_at=NOW - timedelta(days=1),
    )
    db_session.add(stale_session)
    db_session.flush()
    db_session.add(
        ReviewSessionCard(
            review_session_id=stale_session.id,
            card_id=stale_card.id,
            position=0,
        )
    )
    for number in range(10):
        make_active_card(db_session, user, question=f"Fresh normal {number}")
    for number in range(3):
        make_active_card(
            db_session,
            user,
            question=f"Fresh skeleton {number}",
            card_type=CardType.SKELETON_RECALL,
        )

    review_session = get_or_create_daily_session(db_session, user_id=user.id, now=NOW)

    assert review_session is not None
    assert review_session.id != stale_session.id
    assert stale_session.completed_at == NOW
    assert queued_card_types(db_session, review_session) == Counter(
        {CardType.NORMAL: 10, CardType.SKELETON_RECALL: 3}
    )


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
