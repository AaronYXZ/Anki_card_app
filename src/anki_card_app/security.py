from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from starlette.responses import Response

from anki_card_app.config import get_settings

CSRF_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "connect-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "img-src 'self' data:",
        "manifest-src 'self'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "worker-src 'self'",
    )
)


def csrf_token_for_session(session_token: str) -> str:
    return hashlib.sha256(f"anki-card-app:csrf:{session_token}".encode()).hexdigest()


def prepare_csrf_token(request: Request) -> str | None:
    settings = get_settings()
    session_token = request.cookies.get(settings.session_cookie_name, "")
    if settings.auth_mode == "password" and session_token:
        token = csrf_token_for_session(session_token)
        new_cookie = None
    else:
        cookie_token = request.cookies.get(settings.csrf_cookie_name, "")
        if CSRF_TOKEN_PATTERN.fullmatch(cookie_token):
            token = cookie_token
            new_cookie = None
        else:
            token = secrets.token_urlsafe(32)
            new_cookie = token
    request.scope["csrf_token"] = token
    return new_cookie


def set_csrf_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=365 * 24 * 60 * 60,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="lax",
    )


def add_security_headers(response: Response) -> None:
    settings = get_settings()
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


async def validate_csrf(request: Request) -> None:
    expected = request.scope.get("csrf_token", "")
    submitted = request.headers.get("X-CSRF-Token", "")
    if not submitted:
        form = await request.form()
        form_value = form.get("csrf_token", "")
        submitted = form_value if isinstance(form_value, str) else ""
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        )


CsrfProtection = Annotated[None, Depends(validate_csrf)]
