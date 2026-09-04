from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from anki_card_app.analytics_service import dashboard_metrics
from anki_card_app.auth import get_current_user
from anki_card_app.card_service import (
    CardContent,
    CardError,
    CardNotFoundError,
    CardValidationError,
    approve_card,
    create_draft,
    edit_card,
    get_current_version,
    get_owned_card,
    reject_card,
    set_card_favorite,
)
from anki_card_app.database import get_session
from anki_card_app.leetcode_service import (
    LeetCodeFollowUp,
    LeetCodeNoteContent,
    add_leetcode_follow_up,
    create_leetcode_note,
    get_owned_leetcode_note,
    leetcode_content_from_note,
)
from anki_card_app.markdown import render_markdown
from anki_card_app.models import (
    Card,
    CardState,
    CardType,
    CardVersion,
    ReviewSession,
    SourceChunk,
    SourceDocument,
)
from anki_card_app.review_service import (
    ReviewError,
    ReviewNotFoundError,
    get_next_entry,
    get_or_create_daily_session,
    reveal_answer,
    session_rating_counts,
    submit_review,
)
from anki_card_app.security import validate_csrf

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
templates.env.filters["markdown"] = render_markdown
router = APIRouter()
SessionDependency = Annotated[Session, Depends(get_session)]

CLOZE_RENDER_PATTERN = re.compile(r"{{c\d+::(.*?)(?:::[^{}]*)?}}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class CardView:
    card: Card
    version: CardVersion
    prompt: str
    answer: str


def current_user_id(request: Request, session: Session) -> uuid.UUID:
    return get_current_user(request, session).id


def make_card_view(card: Card, version: CardVersion) -> CardView:
    if card.card_type in {CardType.NORMAL, CardType.SKELETON_RECALL}:
        return CardView(
            card=card,
            version=version,
            prompt=version.front or "",
            answer=version.back or "",
        )

    cloze_text = version.cloze_text or ""
    prompt = CLOZE_RENDER_PATTERN.sub("[…]", cloze_text)
    answer = CLOZE_RENDER_PATTERN.sub(lambda match: match.group(1), cloze_text)
    if version.back_extra:
        answer = f"{answer}\n\n{version.back_extra}"
    return CardView(card=card, version=version, prompt=prompt, answer=answer)


def card_views_for_state(
    session: Session, *, user_id: uuid.UUID, card_state: CardState
) -> list[CardView]:
    rows = session.execute(
        select(Card, CardVersion)
        .join(CardVersion, CardVersion.id == Card.current_version_id)
        .where(Card.user_id == user_id, Card.state == card_state)
        .order_by(Card.created_at.desc())
    ).all()
    return [make_card_view(card, version) for card, version in rows]


def favorite_card_views(session: Session, *, user_id: uuid.UUID) -> list[CardView]:
    rows = session.execute(
        select(Card, CardVersion)
        .join(CardVersion, CardVersion.id == Card.current_version_id)
        .where(Card.user_id == user_id, Card.is_favorite.is_(True))
        .order_by(Card.favorited_at.desc(), Card.id)
    ).all()
    return [make_card_view(card, version) for card, version in rows]


def _timestamp(value: datetime) -> float:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.timestamp()


def _source_excerpt_position(chunk: SourceChunk | None, version: CardVersion) -> int:
    if chunk is None or not version.source_excerpt:
        return 0
    position = chunk.text.find(version.source_excerpt)
    return position if position >= 0 else len(chunk.text)


def draft_card_views(session: Session, *, user_id: uuid.UUID) -> list[CardView]:
    query_rows = session.execute(
        select(Card, CardVersion, SourceChunk, SourceDocument)
        .join(CardVersion, CardVersion.id == Card.current_version_id)
        .outerjoin(SourceChunk, SourceChunk.id == Card.source_chunk_id)
        .outerjoin(SourceDocument, SourceDocument.id == Card.source_document_id)
        .where(Card.user_id == user_id, Card.state == CardState.DRAFT)
    ).all()
    rows: list[tuple[Card, CardVersion, SourceChunk | None, SourceDocument | None]] = [
        (card, version, chunk, document) for card, version, chunk, document in query_rows
    ]

    def source_order(
        row: tuple[Card, CardVersion, SourceChunk | None, SourceDocument | None],
    ) -> tuple[float, str, int, int, float, str]:
        card, version, chunk, document = row
        batch_time = document.imported_at if document is not None else card.created_at
        group_id = str(document.id) if document is not None else str(card.id)
        chunk_sequence = chunk.sequence if chunk is not None else 0
        return (
            -_timestamp(batch_time),
            group_id,
            chunk_sequence,
            _source_excerpt_position(chunk, version),
            _timestamp(card.created_at),
            str(card.id),
        )

    rows.sort(key=source_order)
    return [make_card_view(card, version) for card, version, _, _ in rows]


def adjacent_draft_id(
    session: Session, *, user_id: uuid.UUID, card_id: uuid.UUID
) -> uuid.UUID | None:
    draft_ids = [view.card.id for view in draft_card_views(session, user_id=user_id)]
    try:
        position = draft_ids.index(card_id)
    except ValueError:
        return None
    if position + 1 < len(draft_ids):
        return draft_ids[position + 1]
    if position > 0:
        return draft_ids[position - 1]
    return None


def draft_destination(card_id: uuid.UUID | None = None) -> str:
    return f"/cards/drafts#card-{card_id}" if card_id is not None else "/cards/drafts"


def parse_card_type(value: str) -> CardType:
    try:
        return CardType(value)
    except ValueError as error:
        raise CardValidationError(
            "Choose Normal, Cloze, or Skeleton Recall. LeetCode Problem is also available."
        ) from error


def raise_http_card_error(error: CardError) -> NoReturn:
    if isinstance(error, CardNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(request, session)
    metrics = dashboard_metrics(session, user_id=user_id)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"metrics": metrics},
    )


@router.get("/install", response_class=HTMLResponse)
def install_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="install.html", context={})


@router.get("/cards/new", response_class=HTMLResponse)
def new_card_form(request: Request, session: SessionDependency) -> HTMLResponse:
    current_user_id(request, session)
    return templates.TemplateResponse(
        request=request,
        name="card_form.html",
        context={"mode": "create", "card": None, "version": None, "error": None},
    )


@router.post("/cards/new", dependencies=[Depends(validate_csrf)])
def create_card_action(
    request: Request,
    card_type: Annotated[str, Form()],
    session: SessionDependency,
    front: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
    cloze_text: Annotated[str, Form()] = "",
    back_extra: Annotated[str, Form()] = "",
    problem_id: Annotated[str, Form()] = "",
    problem_summary: Annotated[str, Form()] = "",
    pattern: Annotated[str, Form()] = "",
    invariant: Annotated[str, Form()] = "",
    base_approach: Annotated[str, Form()] = "",
    python_skeleton: Annotated[str, Form()] = "",
    complexity: Annotated[str, Form()] = "",
    follow_up_1_question: Annotated[str, Form()] = "",
    follow_up_1_answer: Annotated[str, Form()] = "",
    follow_up_2_question: Annotated[str, Form()] = "",
    follow_up_2_answer: Annotated[str, Form()] = "",
    follow_up_3_question: Annotated[str, Form()] = "",
    follow_up_3_answer: Annotated[str, Form()] = "",
) -> Response:
    user_id = current_user_id(request, session)
    form_values = {
        "card_type": card_type,
        "front": front,
        "back": back,
        "cloze_text": cloze_text,
        "back_extra": back_extra,
        "problem_id": problem_id,
        "problem_summary": problem_summary,
        "pattern": pattern,
        "invariant": invariant,
        "base_approach": base_approach,
        "python_skeleton": python_skeleton,
        "complexity": complexity,
        "follow_up_1_question": follow_up_1_question,
        "follow_up_1_answer": follow_up_1_answer,
        "follow_up_2_question": follow_up_2_question,
        "follow_up_2_answer": follow_up_2_answer,
        "follow_up_3_question": follow_up_3_question,
        "follow_up_3_answer": follow_up_3_answer,
    }
    try:
        if card_type == "leetcode":
            create_leetcode_note(
                session,
                user_id=user_id,
                content=LeetCodeNoteContent(
                    problem_id=problem_id,
                    problem_summary=problem_summary,
                    pattern=pattern,
                    invariant=invariant,
                    base_approach=base_approach,
                    python_skeleton=python_skeleton,
                    complexity=complexity,
                    follow_ups=(
                        LeetCodeFollowUp(follow_up_1_question, follow_up_1_answer),
                        LeetCodeFollowUp(follow_up_2_question, follow_up_2_answer),
                        LeetCodeFollowUp(follow_up_3_question, follow_up_3_answer),
                    ),
                ),
            )
        else:
            create_draft(
                session,
                user_id=user_id,
                card_type=parse_card_type(card_type),
                content=CardContent(
                    front=front,
                    back=back,
                    cloze_text=cloze_text,
                    back_extra=back_extra,
                ),
            )
        session.commit()
    except CardError as error:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="card_form.html",
            context={
                "mode": "create",
                "card": None,
                "version": None,
                "error": str(error),
                "form_values": form_values,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return RedirectResponse("/cards/drafts", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/cards/drafts", response_class=HTMLResponse)
def draft_inbox(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(request, session)
    cards = draft_card_views(session, user_id=user_id)
    return templates.TemplateResponse(
        request=request,
        name="drafts.html",
        context={"cards": cards},
    )


@router.get("/cards", response_class=HTMLResponse)
def approved_cards(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(request, session)
    cards = card_views_for_state(session, user_id=user_id, card_state=CardState.ACTIVE)
    return templates.TemplateResponse(
        request=request,
        name="cards.html",
        context={"cards": cards},
    )


@router.get("/favorites", response_class=HTMLResponse)
def favorite_cards(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(request, session)
    cards = favorite_card_views(session, user_id=user_id)
    return templates.TemplateResponse(
        request=request,
        name="favorites.html",
        context={"cards": cards},
    )


@router.get("/cards/{card_id}", response_class=HTMLResponse)
def card_preview(
    request: Request,
    card_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    user_id = current_user_id(request, session)
    try:
        card = get_owned_card(session, user_id=user_id, card_id=card_id)
        version = get_current_version(session, card)
    except CardError as error:
        raise_http_card_error(error)
    return templates.TemplateResponse(
        request=request,
        name="card_preview.html",
        context={"card_view": make_card_view(card, version)},
    )


@router.get("/cards/{card_id}/edit", response_class=HTMLResponse)
def edit_card_form(
    request: Request,
    card_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    user_id = current_user_id(request, session)
    try:
        card = get_owned_card(session, user_id=user_id, card_id=card_id)
        if card.note_id is not None:
            note = get_owned_leetcode_note(session, user_id=user_id, note_id=card.note_id)
            return templates.TemplateResponse(
                request=request,
                name="leetcode_note_form.html",
                context={
                    "card": card,
                    "content": leetcode_content_from_note(note),
                    "error": None,
                    "form_values": {},
                },
            )
        version = get_current_version(session, card)
    except CardError as error:
        raise_http_card_error(error)
    return templates.TemplateResponse(
        request=request,
        name="card_form.html",
        context={"mode": "edit", "card": card, "version": version, "error": None},
    )


@router.post("/cards/{card_id}/edit", dependencies=[Depends(validate_csrf)])
def edit_card_action(
    request: Request,
    card_id: uuid.UUID,
    session: SessionDependency,
    front: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
    cloze_text: Annotated[str, Form()] = "",
    back_extra: Annotated[str, Form()] = "",
    follow_up_question: Annotated[str, Form()] = "",
    follow_up_answer: Annotated[str, Form()] = "",
) -> Response:
    user_id = current_user_id(request, session)
    try:
        card = get_owned_card(session, user_id=user_id, card_id=card_id)
        if card.note_id is not None:
            new_card = add_leetcode_follow_up(
                session,
                user_id=user_id,
                note_id=card.note_id,
                follow_up=LeetCodeFollowUp(follow_up_question, follow_up_answer),
            )
            session.commit()
            return RedirectResponse(
                draft_destination(new_card.id), status_code=status.HTTP_303_SEE_OTHER
            )
        edit_card(
            session,
            user_id=user_id,
            card_id=card_id,
            content=CardContent(
                front=front,
                back=back,
                cloze_text=cloze_text,
                back_extra=back_extra,
            ),
        )
        session.commit()
    except CardError as error:
        session.rollback()
        if isinstance(error, CardNotFoundError):
            raise_http_card_error(error)
        if card.note_id is not None:
            note = get_owned_leetcode_note(session, user_id=user_id, note_id=card.note_id)
            return templates.TemplateResponse(
                request=request,
                name="leetcode_note_form.html",
                context={
                    "card": card,
                    "content": leetcode_content_from_note(note),
                    "error": str(error),
                    "form_values": {
                        "follow_up_question": follow_up_question,
                        "follow_up_answer": follow_up_answer,
                    },
                },
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return templates.TemplateResponse(
            request=request,
            name="card_form.html",
            context={
                "mode": "edit",
                "card": card,
                "version": CardVersion(
                    front=front,
                    back=back,
                    cloze_text=cloze_text,
                    back_extra=back_extra,
                ),
                "error": str(error),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    destination = (
        draft_destination(card.id) if card.state is CardState.DRAFT else f"/cards/{card.id}"
    )
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cards/{card_id}/approve", dependencies=[Depends(validate_csrf)])
def approve_card_action(
    request: Request, card_id: uuid.UUID, session: SessionDependency
) -> RedirectResponse:
    user_id = current_user_id(request, session)
    next_card_id = adjacent_draft_id(session, user_id=user_id, card_id=card_id)
    try:
        approve_card(session, user_id=user_id, card_id=card_id)
        session.commit()
    except CardError as error:
        session.rollback()
        raise_http_card_error(error)
    return RedirectResponse(draft_destination(next_card_id), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cards/{card_id}/reject", dependencies=[Depends(validate_csrf)])
def reject_card_action(
    request: Request, card_id: uuid.UUID, session: SessionDependency
) -> RedirectResponse:
    user_id = current_user_id(request, session)
    next_card_id = adjacent_draft_id(session, user_id=user_id, card_id=card_id)
    try:
        reject_card(session, user_id=user_id, card_id=card_id)
        session.commit()
    except CardError as error:
        session.rollback()
        raise_http_card_error(error)
    return RedirectResponse(draft_destination(next_card_id), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cards/{card_id}/favorite", dependencies=[Depends(validate_csrf)])
def favorite_card_action(
    request: Request,
    card_id: uuid.UUID,
    favorite: Annotated[bool, Form()],
    session: SessionDependency,
) -> RedirectResponse:
    user_id = current_user_id(request, session)
    try:
        set_card_favorite(
            session,
            user_id=user_id,
            card_id=card_id,
            is_favorite=favorite,
        )
        session.commit()
    except CardError as error:
        session.rollback()
        raise_http_card_error(error)
    return RedirectResponse("/review", status_code=status.HTTP_303_SEE_OTHER)


def raise_http_review_error(error: ReviewError) -> NoReturn:
    code = (
        status.HTTP_404_NOT_FOUND
        if isinstance(error, ReviewNotFoundError)
        else status.HTTP_409_CONFLICT
    )
    raise HTTPException(status_code=code, detail=str(error)) from error


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(request, session)
    review_session = get_or_create_daily_session(session, user_id=user_id)
    if review_session is None:
        session.commit()
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={"entry": None, "card_view": None},
        )
    entry = get_next_entry(
        session,
        user_id=user_id,
        session_id=review_session.id,
    )
    if entry is None:
        session.commit()
        return templates.TemplateResponse(
            request=request,
            name="review.html",
            context={"entry": None, "card_view": None},
        )
    card_view = make_card_view(entry.card, entry.version)
    attempt_id = uuid.uuid4() if entry.item.revealed_at is not None else None
    session.commit()
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={"entry": entry, "card_view": card_view, "attempt_id": attempt_id},
    )


@router.post(
    "/review/{session_id}/{card_id}/reveal",
    dependencies=[Depends(validate_csrf)],
)
def reveal_review_answer(
    request: Request,
    session_id: uuid.UUID,
    card_id: uuid.UUID,
    session: SessionDependency,
) -> RedirectResponse:
    user_id = current_user_id(request, session)
    try:
        reveal_answer(
            session,
            user_id=user_id,
            session_id=session_id,
            card_id=card_id,
        )
        session.commit()
    except ReviewError as error:
        session.rollback()
        raise_http_review_error(error)
    return RedirectResponse("/review", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/review/{session_id}/{card_id}/rate",
    dependencies=[Depends(validate_csrf)],
)
def rate_review_card(
    request: Request,
    session_id: uuid.UUID,
    card_id: uuid.UUID,
    rating: Annotated[int, Form()],
    attempt_id: Annotated[uuid.UUID, Form()],
    session: SessionDependency,
) -> RedirectResponse:
    user_id = current_user_id(request, session)
    try:
        result = submit_review(
            session,
            user_id=user_id,
            session_id=session_id,
            card_id=card_id,
            attempt_id=attempt_id,
            rating=rating,
        )
        session.commit()
    except ReviewError as error:
        session.rollback()
        raise_http_review_error(error)
    destination = f"/review/sessions/{session_id}" if result.session_completed else "/review"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/review/sessions/{session_id}", response_class=HTMLResponse)
def review_session_summary(
    request: Request,
    session_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    user_id = current_user_id(request, session)
    try:
        counts = session_rating_counts(session, user_id=user_id, session_id=session_id)
    except ReviewError as error:
        raise_http_review_error(error)
    review_session = session.get(ReviewSession, session_id)
    return templates.TemplateResponse(
        request=request,
        name="review_summary.html",
        context={"review_session": review_session, "counts": counts},
    )
