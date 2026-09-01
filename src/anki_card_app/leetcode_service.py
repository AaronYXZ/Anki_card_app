from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.card_service import (
    CardContent,
    CardValidationError,
    clean_optional,
    create_draft,
)
from anki_card_app.models import Card, CardType, NoteType, StudyNote


@dataclass(frozen=True, slots=True)
class LeetCodeFollowUp:
    question: str = ""
    answer: str = ""


@dataclass(frozen=True, slots=True)
class LeetCodeNoteContent:
    problem_id: str
    problem_summary: str
    pattern: str
    invariant: str
    base_approach: str
    python_skeleton: str
    complexity: str
    follow_ups: tuple[LeetCodeFollowUp, ...] = ()


@dataclass(frozen=True, slots=True)
class LeetCodeNoteResult:
    note: StudyNote
    cards: tuple[Card, ...]


def _required(value: str, label: str) -> str:
    cleaned = clean_optional(value)
    if cleaned is None:
        raise CardValidationError(f"LeetCode {label} is required.")
    return cleaned


def _normalize(content: LeetCodeNoteContent) -> LeetCodeNoteContent:
    follow_ups: list[LeetCodeFollowUp] = []
    for index, item in enumerate(content.follow_ups, start=1):
        question = clean_optional(item.question)
        answer = clean_optional(item.answer)
        if question is None and answer is None:
            continue
        if question is None or answer is None:
            raise CardValidationError(
                f"LeetCode follow-up {index} requires both a question and an answer."
            )
        follow_ups.append(LeetCodeFollowUp(question=question, answer=answer))
    return LeetCodeNoteContent(
        problem_id=_required(content.problem_id, "problem ID"),
        problem_summary=_required(content.problem_summary, "problem summary"),
        pattern=_required(content.pattern, "pattern"),
        invariant=_required(content.invariant, "invariant"),
        base_approach=_required(content.base_approach, "base approach"),
        python_skeleton=_required(content.python_skeleton, "Python skeleton"),
        complexity=_required(content.complexity, "complexity"),
        follow_ups=tuple(follow_ups),
    )


def _base_heading(content: LeetCodeNoteContent) -> str:
    return f"## {content.problem_id}\n\n{content.problem_summary}"


def create_leetcode_note(
    session: Session,
    *,
    user_id: uuid.UUID,
    content: LeetCodeNoteContent,
) -> LeetCodeNoteResult:
    normalized = _normalize(content)
    if session.scalar(
        select(StudyNote.id).where(
            StudyNote.user_id == user_id,
            StudyNote.note_type == NoteType.LEETCODE,
            StudyNote.fields["problem_id"].as_string() == normalized.problem_id,
        )
    ):
        raise CardValidationError("A LeetCode note with this problem ID already exists.")

    fields = asdict(normalized)
    fields["follow_ups"] = [asdict(item) for item in normalized.follow_ups]
    note = StudyNote(user_id=user_id, note_type=NoteType.LEETCODE, fields=fields)
    session.add(note)
    session.flush()

    heading = _base_heading(normalized)
    card_specs: list[tuple[str, CardContent]] = [
        (
            "pattern",
            CardContent(
                front=(
                    f"{heading}\n\nIdentify:\n\n"
                    "1. Pattern\n2. Recognition signals\n3. Invariant\n4. Complexity"
                ),
                back=(
                    f"**Pattern:** {normalized.pattern}\n\n"
                    f"**Invariant:** {normalized.invariant}\n\n"
                    f"**Approach:** {normalized.base_approach}\n\n"
                    f"**Complexity:** {normalized.complexity}"
                ),
            ),
        ),
        (
            "python",
            CardContent(
                front=(
                    f"{heading}\n\nReconstruct the Python solution. "
                    "Explain the invariant before coding."
                ),
                back=f"```python\n{normalized.python_skeleton}\n```",
            ),
        ),
    ]
    for index, follow_up in enumerate(normalized.follow_ups, start=1):
        card_specs.append(
            (
                f"follow_up_{index}",
                CardContent(
                    front=(
                        f"{heading}\n\n**Interviewer follow-up:**\n\n{follow_up.question}\n\n"
                        "Explain:\n\n1. What breaks?\n2. What replaces it?\n3. New complexity?"
                    ),
                    back=follow_up.answer,
                ),
            )
        )

    cards = tuple(
        create_draft(
            session,
            user_id=user_id,
            card_type=CardType.NORMAL,
            content=card_content,
            created_by="leetcode_template",
            note_id=note.id,
            template_key=template_key,
        )
        for template_key, card_content in card_specs
    )
    return LeetCodeNoteResult(note=note, cards=cards)
