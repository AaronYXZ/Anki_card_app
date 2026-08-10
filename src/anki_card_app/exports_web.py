from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from anki_card_app.auth import CurrentUser
from anki_card_app.database import get_session
from anki_card_app.export_service import build_user_export
from anki_card_app.models import utc_now

router = APIRouter(prefix="/exports", tags=["exports"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/backup.json")
def download_backup(current_user: CurrentUser, session: SessionDependency) -> JSONResponse:
    filename = f"anki-card-app-{utc_now().date().isoformat()}.json"
    return JSONResponse(
        build_user_export(session, user_id=current_user.id),
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
