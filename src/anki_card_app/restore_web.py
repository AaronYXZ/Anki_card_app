from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import Response

from anki_card_app.auth import CurrentUser
from anki_card_app.config import get_settings
from anki_card_app.database import get_session
from anki_card_app.restore_service import (
    RestoreValidationError,
    parse_backup_json,
    restore_user_export,
)
from anki_card_app.security import validate_csrf
from anki_card_app.web import templates

router = APIRouter(prefix="/restore", tags=["restore"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _context(request: Request, *, error: str | None = None) -> dict[str, object]:
    restored = request.query_params.get("restored") == "1"
    return {
        "error": error,
        "restored": restored,
        "restored_cards": request.query_params.get("cards", "0"),
        "restored_reviews": request.query_params.get("reviews", "0"),
        "restored_sources": request.query_params.get("sources", "0"),
    }


@router.get("", response_class=HTMLResponse)
def restore_form(request: Request, current_user: CurrentUser) -> HTMLResponse:
    _ = current_user
    return templates.TemplateResponse(
        request=request,
        name="restore.html",
        context=_context(request),
    )


@router.post("", dependencies=[Depends(validate_csrf)])
async def restore_action(
    request: Request,
    current_user: CurrentUser,
    session: SessionDependency,
    upload: Annotated[UploadFile, File()],
    confirm_restore: Annotated[str, Form()] = "",
) -> Response:
    if confirm_restore != "yes":
        return templates.TemplateResponse(
            request=request,
            name="restore.html",
            context=_context(request, error="Confirm that this account is empty before restoring."),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    settings = get_settings()
    max_backup_bytes = settings.max_archive_uncompressed_bytes
    data = await upload.read(max_backup_bytes + 1)
    if len(data) > max_backup_bytes:
        return templates.TemplateResponse(
            request=request,
            name="restore.html",
            context=_context(request, error="Backup exceeds the configured size limit."),
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    try:
        payload = parse_backup_json(data)
        result = restore_user_export(session, user_id=current_user.id, payload=payload)
        session.commit()
    except RestoreValidationError as error:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="restore.html",
            context=_context(request, error=str(error)),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except SQLAlchemyError:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="restore.html",
            context=_context(
                request,
                error="Backup conflicts with the target database and was not restored.",
            ),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    query = urlencode(
        {
            "restored": "1",
            "cards": result.counts["cards"],
            "reviews": result.counts["review_logs"],
            "sources": result.counts["source_documents"],
        }
    )
    return RedirectResponse(f"/restore?{query}", status_code=status.HTTP_303_SEE_OTHER)
