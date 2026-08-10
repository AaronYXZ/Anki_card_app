from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
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
from anki_card_app.web import current_user_id, templates

router = APIRouter(prefix="/imports", tags=["imports"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _process_in_background(run_id: uuid.UUID, api_key: str, model: str) -> None:
    with Session(get_engine()) as session:
        try:
            process_generation_run(
                session,
                run_id=run_id,
                generator=OpenAICardGenerator(api_key=api_key, model=model),
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
    user_id = current_user_id(session)
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
def import_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="import_form.html",
        context={"error": None},
    )


@router.post("/new")
async def import_action(
    request: Request,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    upload: Annotated[UploadFile, File()],
) -> Response:
    settings = get_settings()
    user_id = current_user_id(session)
    data = await upload.read(settings.max_upload_bytes + 1)
    limits = ImportLimits(
        max_upload_bytes=settings.max_upload_bytes,
        max_archive_files=settings.max_archive_files,
        max_archive_uncompressed_bytes=settings.max_archive_uncompressed_bytes,
    )
    try:
        sources = read_upload(upload.filename or "", data, limits)
        run_ids: list[uuid.UUID] = []
        for source in sources:
            imported = import_markdown(session, user_id=user_id, source=source)
            existing_run = session.scalar(
                select(GenerationRun)
                .where(
                    GenerationRun.user_id == user_id,
                    GenerationRun.source_document_id == imported.document.id,
                )
                .order_by(GenerationRun.created_at.desc())
            )
            if not imported.created and existing_run is not None:
                run_ids.append(existing_run.id)
                continue
            run = create_generation_run(
                session,
                user_id=user_id,
                source_document_id=imported.document.id,
                provider="openai",
                model=settings.openai_model,
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
        session.commit()
    except ImportValidationError as error:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="import_form.html",
            context={"error": str(error)},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if settings.openai_api_key:
        for run_id in run_ids:
            background_tasks.add_task(
                _process_in_background,
                run_id,
                settings.openai_api_key,
                settings.openai_model,
            )
    destination = f"/imports/{run_ids[-1]}" if len(run_ids) == 1 else "/imports"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{run_id}", response_class=HTMLResponse)
def import_detail(
    request: Request,
    run_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    user_id = current_user_id(session)
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
