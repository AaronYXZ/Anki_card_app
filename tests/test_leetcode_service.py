from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.card_service import (
    CardNotFoundError,
    CardValidationError,
    approve_card,
    get_current_version,
)
from anki_card_app.export_service import build_user_export
from anki_card_app.leetcode_service import (
    LeetCodeFollowUp,
    LeetCodeNoteContent,
    add_leetcode_follow_up,
    create_leetcode_note,
)
from anki_card_app.models import Card, NoteType, StudyNote, UserAccount
from anki_card_app.restore_service import restore_user_export
from anki_card_app.review_service import get_or_create_daily_session


def leetcode_content() -> LeetCodeNoteContent:
    return LeetCodeNoteContent(
        problem_id="LC-209 Minimum Size Subarray Sum",
        problem_summary="Find the shortest subarray whose sum is at least target.",
        pattern="Variable sliding window",
        invariant="The window is minimal after shrinking.",
        base_approach="Expand right, then shrink left while valid.",
        python_skeleton="def solve(target: int, nums: list[int]) -> int:\n    return 0",
        complexity="Time O(n), space O(1)",
        follow_ups=(
            LeetCodeFollowUp(
                question="What changes if negative numbers are allowed?",
                answer="Sliding window breaks. Use prefix sums and a monotonic deque. O(n).",
            ),
            LeetCodeFollowUp(),
        ),
    )


@pytest.fixture
def user(db_session: Session) -> UserAccount:
    account = UserAccount(email=f"{uuid.uuid4()}@example.com", daily_limit=25)
    db_session.add(account)
    db_session.flush()
    return account


def test_leetcode_note_generates_only_nonempty_sibling_cards(
    db_session: Session, user: UserAccount
) -> None:
    result = create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())
    db_session.commit()

    assert result.note.note_type is NoteType.LEETCODE
    assert result.note.fields["problem_id"] == "LC-209 Minimum Size Subarray Sum"
    assert [card.template_key for card in result.cards] == ["pattern", "python", "follow_up_1"]
    assert {card.note_id for card in result.cards} == {result.note.id}
    assert len(db_session.scalars(select(Card).where(Card.note_id == result.note.id)).all()) == 3

    pattern = get_current_version(db_session, result.cards[0])
    python = get_current_version(db_session, result.cards[1])
    follow_up = get_current_version(db_session, result.cards[2])
    assert "Recognition signals" in (pattern.front or "")
    assert "Variable sliding window" in (pattern.back or "")
    assert "```python" in (python.back or "")
    assert "negative numbers" in (follow_up.front or "")
    assert "monotonic deque" in (follow_up.back or "")


def test_leetcode_invariant_is_optional(db_session: Session, user: UserAccount) -> None:
    original = leetcode_content()
    content = LeetCodeNoteContent(
        problem_id=original.problem_id,
        problem_summary=original.problem_summary,
        pattern=original.pattern,
        invariant="",
        base_approach=original.base_approach,
        python_skeleton=original.python_skeleton,
        complexity=original.complexity,
    )

    result = create_leetcode_note(db_session, user_id=user.id, content=content)

    assert result.note.fields["invariant"] == ""
    pattern = get_current_version(db_session, result.cards[0])
    python = get_current_version(db_session, result.cards[1])
    assert "Invariant" not in (pattern.front or "")
    assert "**Invariant:**" not in (pattern.back or "")
    assert "Explain the approach before coding" in (python.front or "")


def test_add_follow_up_updates_note_and_creates_next_draft_sibling(
    db_session: Session, user: UserAccount
) -> None:
    result = create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())

    new_card = add_leetcode_follow_up(
        db_session,
        user_id=user.id,
        note_id=result.note.id,
        follow_up=LeetCodeFollowUp(
            question="How would you return the selected subarray?",
            answer="Track the best left and right boundaries when updating the answer.",
        ),
    )
    db_session.commit()

    db_session.refresh(result.note)
    assert new_card.template_key == "follow_up_2"
    assert new_card.state.value == "draft"
    assert len(result.note.fields["follow_ups"]) == 2
    assert result.note.fields["follow_ups"][1]["question"].startswith("How would")
    version = get_current_version(db_session, new_card)
    assert "return the selected subarray" in (version.front or "")


def test_add_follow_up_requires_complete_content_and_note_ownership(
    db_session: Session, user: UserAccount
) -> None:
    result = create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())

    with pytest.raises(CardValidationError, match="requires both"):
        add_leetcode_follow_up(
            db_session,
            user_id=user.id,
            note_id=result.note.id,
            follow_up=LeetCodeFollowUp(question="Missing answer"),
        )
    with pytest.raises(CardNotFoundError, match="not found"):
        add_leetcode_follow_up(
            db_session,
            user_id=uuid.uuid4(),
            note_id=result.note.id,
            follow_up=LeetCodeFollowUp(question="Question", answer="Answer"),
        )


def test_leetcode_note_validates_pairs_and_duplicate_problem_ids(
    db_session: Session, user: UserAccount
) -> None:
    invalid = leetcode_content()
    invalid = LeetCodeNoteContent(
        problem_id=invalid.problem_id,
        problem_summary=invalid.problem_summary,
        pattern=invalid.pattern,
        invariant=invalid.invariant,
        base_approach=invalid.base_approach,
        python_skeleton=invalid.python_skeleton,
        complexity=invalid.complexity,
        follow_ups=(LeetCodeFollowUp(question="Question without answer"),),
    )
    with pytest.raises(CardValidationError, match="requires both"):
        create_leetcode_note(db_session, user_id=user.id, content=invalid)

    create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())
    with pytest.raises(CardValidationError, match="problem ID already exists"):
        create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())


def test_review_queue_buries_cards_from_the_same_leetcode_note(
    db_session: Session, user: UserAccount
) -> None:
    result = create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())
    for card in result.cards:
        approve_card(db_session, user_id=user.id, card_id=card.id)
    db_session.commit()

    review_session = get_or_create_daily_session(db_session, user_id=user.id)

    assert review_session is not None
    assert review_session.queue_size == 1


def test_leetcode_note_is_user_scoped(db_session: Session, user: UserAccount) -> None:
    create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())
    other = UserAccount(email=f"{uuid.uuid4()}@example.com")
    db_session.add(other)
    db_session.flush()

    other_result = create_leetcode_note(db_session, user_id=other.id, content=leetcode_content())

    assert db_session.scalar(select(StudyNote).where(StudyNote.user_id == other.id)) is not None
    assert other_result.note.user_id == other.id


def test_leetcode_note_and_sibling_relationships_survive_backup_restore(
    db_session: Session, user: UserAccount
) -> None:
    source = create_leetcode_note(db_session, user_id=user.id, content=leetcode_content())
    target = UserAccount(email=f"{uuid.uuid4()}@example.com")
    db_session.add(target)
    db_session.commit()
    payload = build_user_export(db_session, user_id=user.id)

    restore_user_export(db_session, user_id=target.id, payload=payload)
    db_session.commit()

    restored_note = db_session.scalar(select(StudyNote).where(StudyNote.user_id == target.id))
    assert restored_note is not None
    restored_cards = db_session.scalars(
        select(Card).where(Card.user_id == target.id).order_by(Card.template_key)
    ).all()
    assert len(restored_cards) == len(source.cards)
    assert {card.note_id for card in restored_cards} == {restored_note.id}
    assert {card.template_key for card in restored_cards} == {
        "pattern",
        "python",
        "follow_up_1",
    }
