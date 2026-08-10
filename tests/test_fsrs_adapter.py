import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import State

from anki_card_app.fsrs_adapter import (
    ALGORITHM_NAME,
    FSRS_VERSION,
    _json_dict,
    apply_review,
    as_utc,
    card_from_state,
    create_initial_schedule,
    scheduler_from_state,
)
from anki_card_app.models import SchedulingState


def test_known_good_rating_sequence_matches_fsrs_6() -> None:
    started_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    state = create_initial_schedule(
        card_id=uuid.UUID(int=1),
        due_at=started_at,
        desired_retention=0.9,
        enable_fuzzing=False,
    )

    first = apply_review(
        state,
        rating=3,
        reviewed_at=started_at,
        desired_retention=0.9,
        enable_fuzzing=False,
    )
    assert state.scheduler_state == "learning"
    assert state.step == 1
    assert state.due_at == started_at + timedelta(minutes=10)
    assert state.stability == pytest.approx(2.3065)
    assert first.elapsed_days is None

    second = apply_review(
        state,
        rating=3,
        reviewed_at=started_at + timedelta(minutes=10),
        desired_retention=0.9,
        enable_fuzzing=False,
    )
    assert state.scheduler_state == "review"
    assert state.step is None
    assert state.due_at == datetime(2026, 1, 3, 12, 10, tzinfo=UTC)
    assert state.review_count == 2
    assert second.elapsed_days == pytest.approx(10 / 1_440)
    assert second.prior_state["review_count"] == 1
    assert second.new_state["review_count"] == 2


def test_initial_schedule_and_legacy_state_conversion() -> None:
    naive_due = datetime(2026, 1, 1, 12)
    state = create_initial_schedule(
        card_id=uuid.uuid4(),
        due_at=naive_due,
        desired_retention=0.88,
    )
    assert state.algorithm == ALGORITHM_NAME
    assert state.algorithm_version == FSRS_VERSION
    assert state.due_at.tzinfo is UTC
    assert state.parameters is not None
    assert state.parameters["desired_retention"] == 0.88

    legacy = SchedulingState(
        card_id=uuid.uuid4(),
        due_at=naive_due,
        scheduler_state="new",
        step=None,
        algorithm="uninitialized",
        review_count=0,
    )
    card = card_from_state(legacy)
    scheduler = scheduler_from_state(legacy, desired_retention=0.91, enable_fuzzing=False)
    assert card.state is State.Learning
    assert card.step == 0
    assert scheduler.desired_retention == 0.91
    assert scheduler.enable_fuzzing is False


def test_serialized_state_and_timezone_helpers() -> None:
    state = create_initial_schedule(
        card_id=uuid.uuid4(),
        due_at=datetime(2026, 1, 1, tzinfo=UTC),
        desired_retention=0.9,
    )
    card = card_from_state(state)
    scheduler = scheduler_from_state(state, desired_retention=0.5, enable_fuzzing=False)
    assert card.due == state.due_at
    assert scheduler.desired_retention == 0.9
    assert scheduler.enable_fuzzing is False
    assert as_utc(datetime(2026, 1, 1)).tzinfo is UTC
    with pytest.raises(ValueError, match="JSON object"):
        _json_dict("[]")
