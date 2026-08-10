from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.fsrs_adapter import as_utc
from anki_card_app.models import (
    Card,
    CardState,
    ReviewLog,
    ReviewSession,
    SchedulingState,
    UserAccount,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class DashboardMetrics:
    ready_count: int
    due_review_count: int
    overdue_count: int
    new_ready_count: int
    reviewed_today: int
    completed_sessions_today: int
    review_minutes_today: float
    daily_limit: int
    recall_attempts_30d: int
    recall_successes_30d: int
    recall_rate_30d: float | None
    card_counts: dict[str, int]


def _timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return UTC


def _local_day_start(current_time: datetime, timezone: tzinfo) -> datetime:
    local_now = current_time.astimezone(timezone)
    return datetime.combine(local_now.date(), time.min, tzinfo=timezone).astimezone(UTC)


def dashboard_metrics(
    session: Session,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> DashboardMetrics:
    current_time = as_utc(now or utc_now())
    user = session.get(UserAccount, user_id)
    if user is None:
        raise ValueError("User not found.")
    timezone = _timezone(user.timezone)
    today_start = _local_day_start(current_time, timezone)

    card_counts = {
        state.value: session.scalar(
            select(func.count())
            .select_from(Card)
            .where(Card.user_id == user_id, Card.state == state)
        )
        or 0
        for state in CardState
    }
    active_schedule = (
        Card.user_id == user_id,
        Card.state == CardState.ACTIVE,
    )
    ready_count = (
        session.scalar(
            select(func.count())
            .select_from(Card)
            .join(SchedulingState, SchedulingState.card_id == Card.id)
            .where(*active_schedule, SchedulingState.due_at <= current_time)
        )
        or 0
    )
    due_review_count = (
        session.scalar(
            select(func.count())
            .select_from(Card)
            .join(SchedulingState, SchedulingState.card_id == Card.id)
            .where(
                *active_schedule,
                SchedulingState.due_at <= current_time,
                SchedulingState.review_count > 0,
            )
        )
        or 0
    )
    overdue_count = (
        session.scalar(
            select(func.count())
            .select_from(Card)
            .join(SchedulingState, SchedulingState.card_id == Card.id)
            .where(
                *active_schedule,
                SchedulingState.due_at < today_start,
                SchedulingState.review_count > 0,
            )
        )
        or 0
    )
    new_ready_count = (
        session.scalar(
            select(func.count())
            .select_from(Card)
            .join(SchedulingState, SchedulingState.card_id == Card.id)
            .where(
                *active_schedule,
                SchedulingState.due_at <= current_time,
                SchedulingState.review_count == 0,
            )
        )
        or 0
    )
    reviewed_today = (
        session.scalar(
            select(func.count())
            .select_from(ReviewLog)
            .where(ReviewLog.user_id == user_id, ReviewLog.reviewed_at >= today_start)
        )
        or 0
    )
    completed_sessions_today = (
        session.scalar(
            select(func.count())
            .select_from(ReviewSession)
            .where(
                ReviewSession.user_id == user_id,
                ReviewSession.completed_at >= today_start,
            )
        )
        or 0
    )
    response_time_ms = (
        session.scalar(
            select(func.sum(ReviewLog.response_time_ms)).where(
                ReviewLog.user_id == user_id,
                ReviewLog.reviewed_at >= today_start,
            )
        )
        or 0
    )

    recall_window_start = today_start - timedelta(days=29)
    due_logs = session.scalars(
        select(ReviewLog)
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= recall_window_start,
            ReviewLog.was_new.is_(False),
        )
        .order_by(ReviewLog.reviewed_at, ReviewLog.id)
    ).all()
    first_attempts: dict[tuple[uuid.UUID, object], ReviewLog] = {}
    for log in due_logs:
        local_date = as_utc(log.reviewed_at).astimezone(timezone).date()
        first_attempts.setdefault((log.card_id, local_date), log)
    recall_attempts = len(first_attempts)
    recall_successes = sum(log.rating >= 2 for log in first_attempts.values())

    return DashboardMetrics(
        ready_count=ready_count,
        due_review_count=due_review_count,
        overdue_count=overdue_count,
        new_ready_count=new_ready_count,
        reviewed_today=reviewed_today,
        completed_sessions_today=completed_sessions_today,
        review_minutes_today=response_time_ms / 60_000,
        daily_limit=user.daily_limit,
        recall_attempts_30d=recall_attempts,
        recall_successes_30d=recall_successes,
        recall_rate_30d=recall_successes / recall_attempts if recall_attempts else None,
        card_counts=card_counts,
    )
