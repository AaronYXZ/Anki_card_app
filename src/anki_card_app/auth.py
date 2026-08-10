from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from anki_card_app.auth_service import resolve_session
from anki_card_app.config import get_settings
from anki_card_app.database import get_session
from anki_card_app.models import UserAccount
from anki_card_app.user_service import ensure_user

SessionDependency = Annotated[Session, Depends(get_session)]


def get_current_user(request: Request, session: SessionDependency) -> UserAccount:
    settings = get_settings()
    if settings.auth_mode == "development":
        development_account = ensure_user(
            session,
            user_id=settings.development_user_id,
            email=settings.development_user_email,
        )
        session.commit()
        request.scope["auth_user"] = development_account
        return development_account

    token = request.cookies.get(settings.session_cookie_name, "")
    account = resolve_session(session, token=token)
    if account is None:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        location = f"/login?next={quote(target, safe='/')}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": location},
            detail="Authentication required.",
        )
    request.scope["auth_user"] = account
    return account


CurrentUser = Annotated[UserAccount, Depends(get_current_user)]
