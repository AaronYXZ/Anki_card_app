from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.card_service import (
    CardContent,
    CardNotFoundError,
    CardValidationError,
    clean_optional,
    create_draft,
)
from anki_card_app.models import Card, CardType, NoteType, StudyNote, utc_now


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
        invariant=clean_optional(content.invariant) or "",
        base_approach=_required(content.base_approach, "base approach"),
        python_skeleton=_required(content.python_skeleton, "Python skeleton"),
        complexity=_required(content.complexity, "complexity"),
        follow_ups=tuple(follow_ups),
    )


def _base_heading(content: LeetCodeNoteContent) -> str:
    return f"## {content.problem_id}\n\n{content.problem_summary}"


def _pattern_card_content(content: LeetCodeNoteContent) -> CardContent:
    identify_items = ["1. Pattern", "2. Recognition signals"]
    answer_sections = [f"**Pattern:** {content.pattern}"]
    if content.invariant:
        identify_items.append("3. Invariant")
        answer_sections.append(f"**Invariant:** {content.invariant}")
    identify_items.append(f"{len(identify_items) + 1}. Complexity")
    answer_sections.extend(
        [
            f"**Approach:** {content.base_approach}",
            f"**Complexity:** {content.complexity}",
        ]
    )
    return CardContent(
        front=f"{_base_heading(content)}\n\nIdentify:\n\n" + "\n".join(identify_items),
        back="\n\n".join(answer_sections),
    )


def _python_card_content(content: LeetCodeNoteContent) -> CardContent:
    instruction = (
        "Explain the invariant before coding."
        if content.invariant
        else "Explain the approach before coding."
    )
    return CardContent(
        front=f"{_base_heading(content)}\n\nReconstruct the Python solution. {instruction}",
        back=f"```python\n{content.python_skeleton}\n```",
    )


def _follow_up_card_content(
    content: LeetCodeNoteContent, follow_up: LeetCodeFollowUp
) -> CardContent:
    return CardContent(
        front=(
            f"{_base_heading(content)}\n\n**Interviewer follow-up:**\n\n"
            f"{follow_up.question}\n\n"
            "Explain:\n\n1. What breaks?\n2. What replaces it?\n3. New complexity?"
        ),
        back=follow_up.answer,
    )


def leetcode_content_from_note(note: StudyNote) -> LeetCodeNoteContent:
    fields = note.fields
    raw_follow_ups = fields.get("follow_ups", [])
    follow_ups = tuple(
        LeetCodeFollowUp(question=item["question"], answer=item["answer"])
        for item in raw_follow_ups
    )
    return LeetCodeNoteContent(
        problem_id=fields["problem_id"],
        problem_summary=fields["problem_summary"],
        pattern=fields["pattern"],
        invariant=fields.get("invariant", ""),
        base_approach=fields["base_approach"],
        python_skeleton=fields["python_skeleton"],
        complexity=fields["complexity"],
        follow_ups=follow_ups,
    )


def get_owned_leetcode_note(
    session: Session, *, user_id: uuid.UUID, note_id: uuid.UUID
) -> StudyNote:
    note = session.scalar(
        select(StudyNote).where(
            StudyNote.id == note_id,
            StudyNote.user_id == user_id,
            StudyNote.note_type == NoteType.LEETCODE,
        )
    )
    if note is None:
        raise CardNotFoundError("LeetCode note not found.")
    return note


def add_leetcode_follow_up(
    session: Session,
    *,
    user_id: uuid.UUID,
    note_id: uuid.UUID,
    follow_up: LeetCodeFollowUp,
) -> Card:
    note = get_owned_leetcode_note(session, user_id=user_id, note_id=note_id)
    content = leetcode_content_from_note(note)
    normalized = _normalize(
        LeetCodeNoteContent(
            problem_id=content.problem_id,
            problem_summary=content.problem_summary,
            pattern=content.pattern,
            invariant=content.invariant,
            base_approach=content.base_approach,
            python_skeleton=content.python_skeleton,
            complexity=content.complexity,
            follow_ups=(follow_up,),
        )
    )
    if not normalized.follow_ups:
        raise CardValidationError("A follow-up question and answer are required.")
    new_follow_up = normalized.follow_ups[0]
    next_index = len(content.follow_ups) + 1
    updated_content = LeetCodeNoteContent(
        problem_id=content.problem_id,
        problem_summary=content.problem_summary,
        pattern=content.pattern,
        invariant=content.invariant,
        base_approach=content.base_approach,
        python_skeleton=content.python_skeleton,
        complexity=content.complexity,
        follow_ups=(*content.follow_ups, new_follow_up),
    )
    fields = asdict(updated_content)
    fields["follow_ups"] = [asdict(item) for item in updated_content.follow_ups]
    note.fields = fields
    note.updated_at = utc_now()
    card = create_draft(
        session,
        user_id=user_id,
        card_type=CardType.NORMAL,
        content=_follow_up_card_content(updated_content, new_follow_up),
        created_by="leetcode_template",
        note_id=note.id,
        template_key=f"follow_up_{next_index}",
    )
    session.flush()
    return card


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

    card_specs: list[tuple[str, CardContent]] = [
        ("pattern", _pattern_card_content(normalized)),
        ("python", _python_card_content(normalized)),
    ]
    for index, follow_up in enumerate(normalized.follow_ups, start=1):
        card_specs.append(
            (
                f"follow_up_{index}",
                _follow_up_card_content(normalized, follow_up),
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
