from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version

from fsrs import Card as FsrsCard
from fsrs import Rating, Scheduler, State

from anki_card_app.models import SchedulingState

FSRS_VERSION = version("fsrs")
ALGORITHM_NAME = "fsrs"


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_dict(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("FSRS serialization must be a JSON object.")
    return parsed


def make_scheduler(*, desired_retention: float, enable_fuzzing: bool = True) -> Scheduler:
    return Scheduler(desired_retention=desired_retention, enable_fuzzing=enable_fuzzing)


def create_initial_schedule(
    *,
    card_id: uuid.UUID,
    due_at: datetime,
    desired_retention: float,
    enable_fuzzing: bool = True,
) -> SchedulingState:
    normalized_due = as_utc(due_at)
    scheduler = make_scheduler(
        desired_retention=desired_retention,
        enable_fuzzing=enable_fuzzing,
    )
    fsrs_card = FsrsCard(
        card_id=card_id.int % (2**63 - 1),
        state=State.Learning,
        step=0,
        due=normalized_due,
    )
    return SchedulingState(
        card_id=card_id,
        due_at=normalized_due,
        stability=None,
        difficulty=None,
        scheduler_state=fsrs_card.state.name.casefold(),
        step=fsrs_card.step,
        algorithm=ALGORITHM_NAME,
        algorithm_version=FSRS_VERSION,
        parameters=_json_dict(scheduler.to_json()),
        fsrs_card=_json_dict(fsrs_card.to_json()),
        review_count=0,
    )


def scheduler_from_state(
    state: SchedulingState,
    *,
    desired_retention: float,
    enable_fuzzing: bool | None = None,
) -> Scheduler:
    if state.parameters:
        scheduler = Scheduler.from_json(json.dumps(state.parameters))
        if enable_fuzzing is not None and scheduler.enable_fuzzing != enable_fuzzing:
            config = _json_dict(scheduler.to_json())
            config["enable_fuzzing"] = enable_fuzzing
            scheduler = Scheduler.from_json(json.dumps(config))
        return scheduler
    return make_scheduler(
        desired_retention=desired_retention,
        enable_fuzzing=True if enable_fuzzing is None else enable_fuzzing,
    )


def card_from_state(state: SchedulingState) -> FsrsCard:
    if state.fsrs_card:
        return FsrsCard.from_json(json.dumps(state.fsrs_card))
    state_map = {
        "new": State.Learning,
        "learning": State.Learning,
        "review": State.Review,
        "relearning": State.Relearning,
    }
    return FsrsCard(
        card_id=state.card_id.int % (2**63 - 1),
        state=state_map.get(state.scheduler_state, State.Learning),
        step=state.step if state.step is not None else 0,
        stability=state.stability,
        difficulty=state.difficulty,
        due=as_utc(state.due_at),
        last_review=as_utc(state.last_review_at) if state.last_review_at else None,
    )


def scheduling_snapshot(state: SchedulingState) -> dict[str, object]:
    return {
        "due_at": as_utc(state.due_at).isoformat(),
        "stability": state.stability,
        "difficulty": state.difficulty,
        "scheduler_state": state.scheduler_state,
        "step": state.step,
        "algorithm": state.algorithm,
        "algorithm_version": state.algorithm_version,
        "parameters": state.parameters,
        "fsrs_card": state.fsrs_card,
        "review_count": state.review_count,
        "last_review_at": (
            as_utc(state.last_review_at).isoformat() if state.last_review_at else None
        ),
    }


@dataclass(frozen=True, slots=True)
class FsrsReviewResult:
    prior_state: dict[str, object]
    new_state: dict[str, object]
    elapsed_days: float | None


def apply_review(
    state: SchedulingState,
    *,
    rating: int,
    reviewed_at: datetime,
    desired_retention: float,
    review_duration_ms: int | None = None,
    enable_fuzzing: bool | None = None,
) -> FsrsReviewResult:
    normalized_reviewed_at = as_utc(reviewed_at)
    prior = scheduling_snapshot(state)
    previous_review = as_utc(state.last_review_at) if state.last_review_at else None
    scheduler = scheduler_from_state(
        state,
        desired_retention=desired_retention,
        enable_fuzzing=enable_fuzzing,
    )
    fsrs_card = card_from_state(state)
    updated, _ = scheduler.review_card(
        fsrs_card,
        Rating(rating),
        review_datetime=normalized_reviewed_at,
        review_duration=review_duration_ms,
    )
    state.due_at = updated.due
    state.stability = updated.stability
    state.difficulty = updated.difficulty
    state.scheduler_state = updated.state.name.casefold()
    state.step = updated.step
    state.algorithm = ALGORITHM_NAME
    state.algorithm_version = FSRS_VERSION
    state.parameters = _json_dict(scheduler.to_json())
    state.fsrs_card = _json_dict(updated.to_json())
    state.review_count += 1
    state.last_review_at = normalized_reviewed_at
    state.updated_at = normalized_reviewed_at
    elapsed_days = (
        (normalized_reviewed_at - previous_review).total_seconds() / 86_400
        if previous_review
        else None
    )
    return FsrsReviewResult(
        prior_state=prior,
        new_state=scheduling_snapshot(state),
        elapsed_days=elapsed_days,
    )
