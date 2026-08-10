from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from anki_card_app.auth import SessionDependency
from anki_card_app.auth_service import authenticate, create_session, revoke_session
from anki_card_app.config import get_settings
from anki_card_app.security import validate_csrf

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(tags=["authentication"])


def safe_next_path(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/") -> Response:
    if get_settings().auth_mode == "development":
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "email": "", "next": safe_next_path(next)},
    )


@router.post("/login", dependencies=[Depends(validate_csrf)])
def login_action(
    request: Request,
    session: SessionDependency,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
) -> Response:
    settings = get_settings()
    if settings.auth_mode == "development":
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    account = authenticate(session, email=email, password=password)
    if account is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Invalid email or password.",
                "email": email,
                "next": safe_next_path(next),
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    revoke_session(
        session,
        token=request.cookies.get(settings.session_cookie_name, ""),
    )
    created = create_session(
        session,
        user_id=account.id,
        lifetime=timedelta(days=settings.session_lifetime_days),
    )
    session.commit()
    response = RedirectResponse(safe_next_path(next), status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        settings.session_cookie_name,
        created.token,
        max_age=settings.session_lifetime_days * 24 * 60 * 60,
        expires=created.expires_at,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout", dependencies=[Depends(validate_csrf)])
def logout(request: Request, session: SessionDependency) -> RedirectResponse:
    settings = get_settings()
    revoke_session(
        session,
        token=request.cookies.get(settings.session_cookie_name, ""),
    )
    session.commit()
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response
