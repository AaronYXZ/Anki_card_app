import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from anki_card_app.analytics_service import dashboard_metrics
from anki_card_app.card_service import CardContent, approve_card, create_draft
from anki_card_app.models import (
    Card,
    CardType,
    ReviewLog,
    ReviewSession,
    SchedulingState,
    UserAccount,
)
from anki_card_app.user_service import ensure_user

NOW = datetime(2026, 8, 10, 18, tzinfo=UTC)


def make_user(db_session: Session, *, timezone: str = "America/Los_Angeles") -> UserAccount:
    identifier = uuid.uuid4()
    user = ensure_user(
        db_session,
        user_id=identifier,
        email=f"{identifier}@example.com",
    )
    user.timezone = timezone
    user.daily_limit = 25
    db_session.flush()
    return user


def make_card(
    db_session: Session,
    user: UserAccount,
    *,
    label: str,
    due_at: datetime | None = None,
    review_count: int | None = None,
) -> Card:
    card = create_draft(
        db_session,
        user_id=user.id,
        card_type=CardType.NORMAL,
        content=CardContent(front=label, back=f"Answer {label}"),
    )
    if due_at is not None:
        approve_card(db_session, user_id=user.id, card_id=card.id, due_at=due_at)
        if review_count is not None:
            scheduling = db_session.get(SchedulingState, card.id)
            assert scheduling is not None
            scheduling.review_count = review_count
    return card


def add_log(
    db_session: Session,
    *,
    user: UserAccount,
    card: Card,
    reviewed_at: datetime,
    rating: int,
    was_new: bool = False,
    response_time_ms: int = 30_000,
    review_session_id: uuid.UUID | None = None,
) -> None:
    db_session.add(
        ReviewLog(
            attempt_id=uuid.uuid4(),
            user_id=user.id,
            card_id=card.id,
            review_session_id=review_session_id,
            rating=rating,
            reviewed_at=reviewed_at,
            response_time_ms=response_time_ms,
            was_new=was_new,
            elapsed_days=1,
            prior_state={},
            new_state={},
        )
    )


def test_dashboard_metrics_and_first_attempt_recall(db_session: Session) -> None:
    user = make_user(db_session)
    new_card = make_card(db_session, user, label="New", due_at=NOW)
    due_card = make_card(db_session, user, label="Due", due_at=NOW, review_count=1)
    overdue_card = make_card(
        db_session,
        user,
        label="Overdue",
        due_at=NOW - timedelta(days=2),
        review_count=3,
    )
    make_card(
        db_session,
        user,
        label="Future",
        due_at=NOW + timedelta(days=1),
        review_count=1,
    )
    make_card(db_session, user, label="Draft")
    review_session = ReviewSession(
        user_id=user.id,
        queue_size=1,
        reviewed_count=1,
        started_at=NOW - timedelta(minutes=3),
        completed_at=NOW - timedelta(minutes=1),
    )
    db_session.add(review_session)
    db_session.flush()
    add_log(
        db_session,
        user=user,
        card=due_card,
        reviewed_at=NOW - timedelta(hours=2),
        rating=1,
        response_time_ms=60_000,
        review_session_id=review_session.id,
    )
    add_log(
        db_session,
        user=user,
        card=due_card,
        reviewed_at=NOW - timedelta(hours=1),
        rating=3,
        review_session_id=review_session.id,
    )
    add_log(
        db_session,
        user=user,
        card=overdue_card,
        reviewed_at=NOW - timedelta(days=1),
        rating=2,
    )
    add_log(
        db_session,
        user=user,
        card=new_card,
        reviewed_at=NOW - timedelta(minutes=30),
        rating=1,
        was_new=True,
        review_session_id=review_session.id,
    )
    db_session.flush()

    metrics = dashboard_metrics(db_session, user_id=user.id, now=NOW)

    assert metrics.ready_count == 3
    assert metrics.due_review_count == 2
    assert metrics.overdue_count == 1
    assert metrics.new_ready_count == 1
    assert metrics.reviewed_today == 3
    assert metrics.completed_sessions_today == 1
    assert metrics.review_minutes_today == 2
    assert metrics.card_counts["draft"] == 1
    assert metrics.card_counts["active"] == 4
    assert metrics.recall_attempts_30d == 2
    assert metrics.recall_successes_30d == 1
    assert metrics.recall_rate_30d == 0.5


def test_dashboard_metrics_handles_unknown_timezone_and_no_reviews(
    db_session: Session,
) -> None:
    user = make_user(db_session, timezone="Not/A_Timezone")

    metrics = dashboard_metrics(db_session, user_id=user.id, now=NOW)

    assert metrics.ready_count == 0
    assert metrics.recall_attempts_30d == 0
    assert metrics.recall_rate_30d is None
