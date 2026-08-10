from __future__ import annotations

import uuid
from datetime import UTC, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from anki_card_app.config import get_settings
from anki_card_app.database import get_engine, get_session
from anki_card_app.generation import (
    OpenAICardGenerator,
    create_generation_run,
    process_generation_run,
)
from anki_card_app.import_service import (
    ImportLimits,
    ImportValidationError,
    import_markdown,
    read_upload,
)
from anki_card_app.models import (
    Card,
    ChunkGenerationStatus,
    GenerationChunkRun,
    GenerationRun,
    GenerationStatus,
    SourceChunk,
    SourceDocument,
    utc_now,
)
from anki_card_app.security import validate_csrf
from anki_card_app.web import current_user_id, templates

router = APIRouter(prefix="/imports", tags=["imports"])
SessionDependency = Annotated[Session, Depends(get_session)]
GENERATION_MODEL_OPTIONS = (
    ("gpt-5.6-terra", "Terra"),
    ("gpt-5.6-luna", "Luna"),
)
ALLOWED_GENERATION_MODELS = frozenset(model for model, _ in GENERATION_MODEL_OPTIONS)


def _import_form_context(*, selected_model: str, error: str | None = None) -> dict[str, object]:
    return {
        "error": error,
        "model_options": GENERATION_MODEL_OPTIONS,
        "selected_model": selected_model,
    }


def _process_in_background(
    run_id: uuid.UUID,
    api_key: str,
    model: str,
    timeout_seconds: float = 90.0,
    max_retries: int = 0,
) -> None:
    with Session(get_engine()) as session:
        try:
            process_generation_run(
                session,
                run_id=run_id,
                generator=OpenAICardGenerator(
                    api_key=api_key,
                    model=model,
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                ),
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            run = session.get(GenerationRun, run_id)
            if run is not None:
                run.status = GenerationStatus.FAILED
                run.error_summary = str(exc)[:2_000]
                run.completed_at = utc_now()
                session.commit()


@router.get("", response_class=HTMLResponse)
def import_list(request: Request, session: SessionDependency) -> HTMLResponse:
    user_id = current_user_id(request, session)
    runs = session.execute(
        select(GenerationRun, SourceDocument)
        .join(SourceDocument, SourceDocument.id == GenerationRun.source_document_id)
        .where(GenerationRun.user_id == user_id)
        .order_by(GenerationRun.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="imports.html",
        context={"runs": runs},
    )


@router.get("/new", response_class=HTMLResponse)
def import_form(request: Request, session: SessionDependency) -> HTMLResponse:
    current_user_id(request, session)
    settings = get_settings()
    selected_model = (
        settings.openai_model
        if settings.openai_model in ALLOWED_GENERATION_MODELS
        else GENERATION_MODEL_OPTIONS[0][0]
    )
    return templates.TemplateResponse(
        request=request,
        name="import_form.html",
        context=_import_form_context(selected_model=selected_model),
    )


@router.post("/new", dependencies=[Depends(validate_csrf)])
async def import_action(
    request: Request,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    upload: Annotated[UploadFile, File()],
    model: Annotated[str, Form()] = "",
) -> Response:
    user_id = current_user_id(request, session)
    settings = get_settings()
    selected_model = model.strip() or settings.openai_model
    if selected_model not in ALLOWED_GENERATION_MODELS:
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_import_form_context(
                selected_model=GENERATION_MODEL_OPTIONS[0][0],
                error="Choose Terra or Luna for card generation.",
            ),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    data = await upload.read(settings.max_upload_bytes + 1)
    limits = ImportLimits(
        max_upload_bytes=settings.max_upload_bytes,
        max_archive_files=settings.max_archive_files,
        max_archive_uncompressed_bytes=settings.max_archive_uncompressed_bytes,
    )
    try:
        sources = read_upload(upload.filename or "", data, limits)
        run_ids: list[uuid.UUID] = []
        background_run_ids: list[uuid.UUID] = []
        for source in sources:
            imported = import_markdown(session, user_id=user_id, source=source)
            existing_run = session.scalar(
                select(GenerationRun)
                .where(
                    GenerationRun.user_id == user_id,
                    GenerationRun.source_document_id == imported.document.id,
                    GenerationRun.model == selected_model,
                )
                .order_by(GenerationRun.created_at.desc())
            )
            if not imported.created and existing_run is not None:
                if existing_run.status is GenerationStatus.COMPLETED or not settings.openai_api_key:
                    run_ids.append(existing_run.id)
                    continue
            run = create_generation_run(
                session,
                user_id=user_id,
                source_document_id=imported.document.id,
                provider="openai",
                model=selected_model,
                input_hash=imported.document.content_hash,
            )
            if settings.openai_api_key is None:
                run.status = GenerationStatus.FAILED
                run.error_summary = (
                    "OPENAI_API_KEY is not configured. The source was imported safely."
                )
                run.failed_chunks = run.total_chunks
                run.completed_at = utc_now()
                chunk_runs = session.scalars(
                    select(GenerationChunkRun).where(GenerationChunkRun.generation_run_id == run.id)
                ).all()
                for chunk_run in chunk_runs:
                    chunk_run.status = ChunkGenerationStatus.FAILED
                    chunk_run.error = (
                        "Generation was not started because OPENAI_API_KEY is missing."
                    )
                    chunk_run.completed_at = run.completed_at
            run_ids.append(run.id)
            if settings.openai_api_key:
                background_run_ids.append(run.id)
        session.commit()
    except ImportValidationError as error:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context=_import_form_context(
                selected_model=selected_model,
                error=str(error),
            ),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if settings.openai_api_key:
        for run_id in background_run_ids:
            background_tasks.add_task(
                _process_in_background,
                run_id,
                settings.openai_api_key,
                selected_model,
                settings.openai_timeout_seconds,
                settings.openai_max_retries,
            )
    destination = f"/imports/{run_ids[-1]}" if len(run_ids) == 1 else "/imports"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{run_id}/retry", dependencies=[Depends(validate_csrf)])
def retry_import_generation(
    request: Request,
    run_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
) -> RedirectResponse:
    settings = get_settings()
    user_id = current_user_id(request, session)
    original = session.scalar(
        select(GenerationRun).where(
            GenerationRun.id == run_id,
            GenerationRun.user_id == user_id,
        )
    )
    if original is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found.")
    if original.status is GenerationStatus.COMPLETED:
        return RedirectResponse(f"/imports/{original.id}", status_code=status.HTTP_303_SEE_OTHER)
    if original.status is GenerationStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generation is already running.",
        )
    created_at = (
        original.created_at.replace(tzinfo=UTC)
        if original.created_at.tzinfo is None
        else original.created_at
    )
    if original.status is GenerationStatus.PENDING and created_at > utc_now() - timedelta(
        minutes=2
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generation is still queued. Wait two minutes before resuming.",
        )
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OPENAI_API_KEY is not configured.",
        )
    document = session.get(SourceDocument, original.source_document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    retry_run = create_generation_run(
        session,
        user_id=user_id,
        source_document_id=document.id,
        provider="openai",
        model=original.model,
        input_hash=document.content_hash,
    )
    session.commit()
    background_tasks.add_task(
        _process_in_background,
        retry_run.id,
        settings.openai_api_key,
        original.model,
        settings.openai_timeout_seconds,
        settings.openai_max_retries,
    )
    return RedirectResponse(
        f"/imports/{retry_run.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{run_id}", response_class=HTMLResponse)
def import_detail(
    request: Request,
    run_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    user_id = current_user_id(request, session)
    row = session.execute(
        select(GenerationRun, SourceDocument)
        .join(SourceDocument, SourceDocument.id == GenerationRun.source_document_id)
        .where(GenerationRun.id == run_id, GenerationRun.user_id == user_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found.")
    run, document = row
    chunks = session.execute(
        select(GenerationChunkRun, SourceChunk)
        .join(SourceChunk, SourceChunk.id == GenerationChunkRun.source_chunk_id)
        .where(GenerationChunkRun.generation_run_id == run.id)
        .order_by(SourceChunk.sequence)
    ).all()
    cards = session.scalars(
        select(Card).where(Card.user_id == user_id, Card.generation_run_id == run.id)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="import_detail.html",
        context={"run": run, "document": document, "chunks": chunks, "cards": cards},
    )
