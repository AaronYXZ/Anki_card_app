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
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import Response

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
)
from anki_card_app.config import get_settings
from anki_card_app.database import get_session
from anki_card_app.models import Card, CardState, CardType, CardVersion, SchedulingState
from anki_card_app.user_service import ensure_user

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
router = APIRouter()
SessionDependency = Annotated[Session, Depends(get_session)]

CLOZE_RENDER_PATTERN = re.compile(r"{{c\d+::(.*?)(?:::[^{}]*)?}}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class CardView:
    card: Card
    version: CardVersion
    prompt: str
    answer: str


def current_user_id(session: Session) -> uuid.UUID:
    settings = get_settings()
    ensure_user(
        session,
        user_id=settings.development_user_id,
        email=settings.development_user_email,
    )
    session.commit()
    return settings.development_user_id


def make_card_view(card: Card, version: CardVersion) -> CardView:
    if card.card_type is CardType.NORMAL:
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


def parse_card_type(value: str) -> CardType:
    try:
        return CardType(value)
    except ValueError as error:
        raise CardValidationError("Choose Normal or Cloze.") from error


def raise_http_card_error(error: CardError) -> NoReturn:
    if isinstance(error, CardNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(session)
    counts = {
        state.value: session.scalar(
            select(func.count())
            .select_from(Card)
            .where(Card.user_id == user_id, Card.state == state)
        )
        or 0
        for state in CardState
    }
    due_count = (
        session.scalar(
            select(func.count())
            .select_from(Card)
            .join(SchedulingState, SchedulingState.card_id == Card.id)
            .where(
                Card.user_id == user_id,
                Card.state == CardState.ACTIVE,
                SchedulingState.due_at <= datetime.now(UTC),
            )
        )
        or 0
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"counts": counts, "due_count": due_count},
    )


@router.get("/cards/new", response_class=HTMLResponse)
def new_card_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="card_form.html",
        context={"mode": "create", "card": None, "version": None, "error": None},
    )


@router.post("/cards/new")
def create_card_action(
    request: Request,
    card_type: Annotated[str, Form()],
    session: SessionDependency,
    front: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
    cloze_text: Annotated[str, Form()] = "",
    back_extra: Annotated[str, Form()] = "",
) -> Response:
    user_id = current_user_id(session)
    form_values = {
        "card_type": card_type,
        "front": front,
        "back": back,
        "cloze_text": cloze_text,
        "back_extra": back_extra,
    }
    try:
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
    user_id = current_user_id(session)
    cards = card_views_for_state(session, user_id=user_id, card_state=CardState.DRAFT)
    return templates.TemplateResponse(
        request=request,
        name="drafts.html",
        context={"cards": cards},
    )


@router.get("/cards/{card_id}/edit", response_class=HTMLResponse)
def edit_card_form(
    request: Request,
    card_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    user_id = current_user_id(session)
    try:
        card = get_owned_card(session, user_id=user_id, card_id=card_id)
        version = get_current_version(session, card)
    except CardError as error:
        raise_http_card_error(error)
    return templates.TemplateResponse(
        request=request,
        name="card_form.html",
        context={"mode": "edit", "card": card, "version": version, "error": None},
    )


@router.post("/cards/{card_id}/edit")
def edit_card_action(
    request: Request,
    card_id: uuid.UUID,
    session: SessionDependency,
    front: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
    cloze_text: Annotated[str, Form()] = "",
    back_extra: Annotated[str, Form()] = "",
) -> Response:
    user_id = current_user_id(session)
    try:
        card = get_owned_card(session, user_id=user_id, card_id=card_id)
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
    destination = "/cards/drafts" if card.state is CardState.DRAFT else "/review"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cards/{card_id}/approve")
def approve_card_action(card_id: uuid.UUID, session: SessionDependency) -> RedirectResponse:
    user_id = current_user_id(session)
    try:
        approve_card(session, user_id=user_id, card_id=card_id)
        session.commit()
    except CardError as error:
        session.rollback()
        raise_http_card_error(error)
    return RedirectResponse("/cards/drafts", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cards/{card_id}/reject")
def reject_card_action(card_id: uuid.UUID, session: SessionDependency) -> RedirectResponse:
    user_id = current_user_id(session)
    try:
        reject_card(session, user_id=user_id, card_id=card_id)
        session.commit()
    except CardError as error:
        session.rollback()
        raise_http_card_error(error)
    return RedirectResponse("/cards/drafts", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/review", response_class=HTMLResponse)
def review_preview(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(session)
    row = session.execute(
        select(Card, CardVersion)
        .join(CardVersion, CardVersion.id == Card.current_version_id)
        .join(SchedulingState, SchedulingState.card_id == Card.id)
        .where(
            Card.user_id == user_id,
            Card.state == CardState.ACTIVE,
            SchedulingState.due_at <= datetime.now(UTC),
        )
        .order_by(SchedulingState.due_at, Card.created_at)
        .limit(1)
    ).one_or_none()
    card_view = make_card_view(*row) if row is not None else None
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={"card_view": card_view},
    )
