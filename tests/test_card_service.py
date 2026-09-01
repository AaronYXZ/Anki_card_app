import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.card_service import (
    CardContent,
    CardNotFoundError,
    CardValidationError,
    InvalidCardTransitionError,
    approve_card,
    create_draft,
    edit_card,
    get_current_version,
    get_owned_card,
    reject_card,
    resume_card,
    retire_card,
    set_card_favorite,
    suspend_card,
    validate_content,
)
from anki_card_app.models import Card, CardState, CardType, CardVersion, SchedulingState
from anki_card_app.user_service import ensure_user


@pytest.fixture
def user_id(db_session: Session) -> uuid.UUID:
    identifier = uuid.uuid4()
    ensure_user(db_session, user_id=identifier, email=f"{identifier}@example.com")
    db_session.commit()
    return identifier


def create_normal_draft(db_session: Session, user_id: uuid.UUID) -> Card:
    return create_draft(
        db_session,
        user_id=user_id,
        card_type=CardType.NORMAL,
        content=CardContent(front="What is power?", back="One minus Type II error."),
    )


def test_validate_all_card_type_content() -> None:
    normal = validate_content(
        CardType.NORMAL,
        CardContent(front="  Question  ", back="  Answer  ", cloze_text="ignored"),
    )
    cloze = validate_content(
        CardType.CLOZE,
        CardContent(cloze_text="Power is {{c1::one minus beta}}.", back_extra="Context"),
    )
    skeleton = validate_content(
        CardType.SKELETON_RECALL,
        CardContent(
            front="Debugging an outage\n\n1. Signal\n2. Cause\n3. Fix",
            back="1. Signal\n- Error spike\n\n2. Cause\n- Bad deploy\n\n3. Fix\n- Rollback",
        ),
    )

    assert normal.front == "Question"
    assert normal.back == "Answer"
    assert cloze.cloze_text == "Power is {{c1::one minus beta}}."
    assert skeleton.front is not None
    assert skeleton.front.startswith("Debugging an outage")


@pytest.mark.parametrize(
    ("card_type", "content", "message"),
    [
        (CardType.NORMAL, CardContent(front="Question"), "question and an answer"),
        (
            CardType.SKELETON_RECALL,
            CardContent(front="1. Situation"),
            "outline front and a completed back",
        ),
        (CardType.CLOZE, CardContent(), "require cloze text"),
        (CardType.CLOZE, CardContent(cloze_text="No deletion"), "must include a deletion"),
    ],
)
def test_validate_content_rejects_invalid_cards(
    card_type: CardType, content: CardContent, message: str
) -> None:
    with pytest.raises(CardValidationError, match=message):
        validate_content(card_type, content)


def test_create_and_edit_card_preserves_versions(db_session: Session, user_id: uuid.UUID) -> None:
    card = create_normal_draft(db_session, user_id)
    original = get_current_version(db_session, card)

    replacement = edit_card(
        db_session,
        user_id=user_id,
        card_id=card.id,
        content=CardContent(front="Updated question", back="Updated answer"),
    )
    db_session.commit()

    version_count = db_session.scalar(
        select(func.count()).select_from(CardVersion).where(CardVersion.card_id == card.id)
    )
    assert card.state is CardState.DRAFT
    assert original.version_number == 1
    assert replacement.version_number == 2
    assert card.current_version_id == replacement.id
    assert version_count == 2


def test_card_favorite_is_persistent_idempotent_and_user_scoped(
    db_session: Session, user_id: uuid.UUID
) -> None:
    card = create_normal_draft(db_session, user_id)

    set_card_favorite(
        db_session,
        user_id=user_id,
        card_id=card.id,
        is_favorite=True,
    )
    first_favorited_at = card.favorited_at
    set_card_favorite(
        db_session,
        user_id=user_id,
        card_id=card.id,
        is_favorite=True,
    )
    db_session.commit()

    assert card.is_favorite is True
    assert first_favorited_at is not None
    assert card.favorited_at == first_favorited_at
    set_card_favorite(
        db_session,
        user_id=user_id,
        card_id=card.id,
        is_favorite=False,
    )
    assert card.is_favorite is False
    assert card.favorited_at is None
    with pytest.raises(CardNotFoundError):
        set_card_favorite(
            db_session,
            user_id=uuid.uuid4(),
            card_id=card.id,
            is_favorite=False,
        )


def test_create_and_edit_reject_exact_duplicates(db_session: Session, user_id: uuid.UUID) -> None:
    first = create_normal_draft(db_session, user_id)
    second = create_draft(
        db_session,
        user_id=user_id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Another question", back="Another answer"),
    )

    with pytest.raises(CardValidationError, match="duplicate"):
        create_normal_draft(db_session, user_id)
    with pytest.raises(CardValidationError, match="duplicate"):
        edit_card(
            db_session,
            user_id=user_id,
            card_id=second.id,
            content=CardContent(front="What is power?", back="One minus Type II error."),
        )
    assert first.id != second.id


def test_approve_initializes_due_scheduling_state(db_session: Session, user_id: uuid.UUID) -> None:
    card = create_normal_draft(db_session, user_id)
    due_at = datetime(2026, 8, 10, 12, tzinfo=UTC)

    approve_card(db_session, user_id=user_id, card_id=card.id, due_at=due_at)
    db_session.commit()

    scheduling = db_session.get(SchedulingState, card.id)
    assert card.state is CardState.ACTIVE
    assert scheduling is not None
    assert scheduling.due_at.replace(tzinfo=UTC) == due_at
    assert scheduling.scheduler_state == "learning"
    assert scheduling.algorithm == "fsrs"
    assert scheduling.algorithm_version is not None
    assert scheduling.fsrs_card is not None
    assert scheduling.parameters is not None
    assert scheduling.review_count == 0

    with pytest.raises(InvalidCardTransitionError, match="Only draft"):
        approve_card(db_session, user_id=user_id, card_id=card.id)


def test_reject_draft_blocks_editing(db_session: Session, user_id: uuid.UUID) -> None:
    card = create_normal_draft(db_session, user_id)

    reject_card(db_session, user_id=user_id, card_id=card.id)

    assert card.state is CardState.REJECTED
    with pytest.raises(InvalidCardTransitionError, match="Cannot edit"):
        edit_card(
            db_session,
            user_id=user_id,
            card_id=card.id,
            content=CardContent(front="Question", back="Answer"),
        )
    with pytest.raises(InvalidCardTransitionError, match="Only draft"):
        reject_card(db_session, user_id=user_id, card_id=card.id)


def test_suspend_resume_and_retire_lifecycle(db_session: Session, user_id: uuid.UUID) -> None:
    card = create_normal_draft(db_session, user_id)
    approve_card(db_session, user_id=user_id, card_id=card.id)

    suspend_card(db_session, user_id=user_id, card_id=card.id)
    assert card.state.value == "suspended"
    resume_card(db_session, user_id=user_id, card_id=card.id)
    assert card.state.value == "active"
    suspend_card(db_session, user_id=user_id, card_id=card.id)
    retired_card = retire_card(db_session, user_id=user_id, card_id=card.id)
    assert retired_card.state is CardState.RETIRED

    with pytest.raises(InvalidCardTransitionError, match="Only active"):
        suspend_card(db_session, user_id=user_id, card_id=card.id)
    with pytest.raises(InvalidCardTransitionError, match="Only suspended"):
        resume_card(db_session, user_id=user_id, card_id=card.id)
    with pytest.raises(InvalidCardTransitionError, match="Only active or suspended"):
        retire_card(db_session, user_id=user_id, card_id=card.id)


def test_card_access_is_scoped_to_user(db_session: Session, user_id: uuid.UUID) -> None:
    card = create_normal_draft(db_session, user_id)

    with pytest.raises(CardNotFoundError):
        get_owned_card(db_session, user_id=uuid.uuid4(), card_id=card.id)


def test_current_version_must_exist(db_session: Session, user_id: uuid.UUID) -> None:
    card = create_normal_draft(db_session, user_id)
    current_id = card.current_version_id
    card.current_version_id = None

    with pytest.raises(CardValidationError, match="no current version"):
        get_current_version(db_session, card)

    card.current_version_id = uuid.uuid4()
    with pytest.raises(CardValidationError, match="is missing"):
        get_current_version(db_session, card)
    card.current_version_id = current_id
