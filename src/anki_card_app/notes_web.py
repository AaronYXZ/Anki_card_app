from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.database import get_session
from anki_card_app.models import (
    Card,
    CardState,
    CardVersion,
    GenerationRun,
    SourceChunk,
    SourceDocument,
)
from anki_card_app.web import CardView, current_user_id, make_card_view, templates

router = APIRouter(prefix="/notes", tags=["notes"])
SessionDependency = Annotated[Session, Depends(get_session)]


@dataclass(frozen=True, slots=True)
class NoteSummary:
    document: SourceDocument
    chunk_count: int
    run_count: int
    latest_run: GenerationRun | None
    card_counts: dict[str, int]

    @property
    def total_cards(self) -> int:
        return sum(self.card_counts.values())


@dataclass(frozen=True, slots=True)
class SourcedCardView:
    view: CardView
    heading_path: str | None


def _summaries(session: Session, *, user_id: uuid.UUID) -> list[NoteSummary]:
    documents = session.scalars(
        select(SourceDocument)
        .where(SourceDocument.user_id == user_id)
        .order_by(SourceDocument.imported_at.desc())
    ).all()
    if not documents:
        return []

    document_ids = [document.id for document in documents]
    chunk_counts: dict[uuid.UUID, int] = {
        document_id: count
        for document_id, count in session.execute(
            select(SourceChunk.source_document_id, func.count())
            .where(SourceChunk.source_document_id.in_(document_ids))
            .group_by(SourceChunk.source_document_id)
        )
    }
    runs_by_document: dict[uuid.UUID, list[GenerationRun]] = defaultdict(list)
    for run in session.scalars(
        select(GenerationRun)
        .where(
            GenerationRun.user_id == user_id,
            GenerationRun.source_document_id.in_(document_ids),
        )
        .order_by(GenerationRun.created_at.desc())
    ):
        runs_by_document[run.source_document_id].append(run)

    card_counts: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for document_id, card_state, count in session.execute(
        select(Card.source_document_id, Card.state, func.count())
        .where(Card.user_id == user_id, Card.source_document_id.in_(document_ids))
        .group_by(Card.source_document_id, Card.state)
    ):
        if document_id is not None:
            card_counts[document_id][card_state.value] = count

    return [
        NoteSummary(
            document=document,
            chunk_count=chunk_counts.get(document.id, 0),
            run_count=len(runs_by_document[document.id]),
            latest_run=runs_by_document[document.id][0] if runs_by_document[document.id] else None,
            card_counts=card_counts[document.id],
        )
        for document in documents
    ]


@router.get("", response_class=HTMLResponse)
def note_list(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(session)
    return templates.TemplateResponse(
        request=request,
        name="notes.html",
        context={"notes": _summaries(session, user_id=user_id)},
    )


@router.get("/{document_id}", response_class=HTMLResponse)
def note_detail(
    request: Request,
    document_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    user_id = current_user_id(session)
    document = session.scalar(
        select(SourceDocument).where(
            SourceDocument.id == document_id,
            SourceDocument.user_id == user_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found.")

    summary = _summaries(session, user_id=user_id)
    note = next(item for item in summary if item.document.id == document.id)
    runs = session.scalars(
        select(GenerationRun)
        .where(
            GenerationRun.user_id == user_id,
            GenerationRun.source_document_id == document.id,
        )
        .order_by(GenerationRun.created_at.desc())
    ).all()
    card_rows = session.execute(
        select(Card, CardVersion, SourceChunk.heading_path)
        .join(CardVersion, CardVersion.id == Card.current_version_id)
        .outerjoin(SourceChunk, SourceChunk.id == Card.source_chunk_id)
        .where(Card.user_id == user_id, Card.source_document_id == document.id)
        .order_by(Card.created_at)
    ).all()
    cards = [
        SourcedCardView(view=make_card_view(card, version), heading_path=heading_path)
        for card, version, heading_path in card_rows
    ]
    return templates.TemplateResponse(
        request=request,
        name="note_detail.html",
        context={
            "note": note,
            "document": document,
            "runs": runs,
            "cards": cards,
            "editable_states": {
                CardState.DRAFT.value,
                CardState.ACTIVE.value,
                CardState.SUSPENDED.value,
            },
        },
    )
