from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from anki_card_app.auth_web import router as auth_router
from anki_card_app.config import get_settings
from anki_card_app.database import database_is_ready
from anki_card_app.exports_web import router as exports_router
from anki_card_app.imports_web import router as imports_router
from anki_card_app.notes_web import router as notes_router
from anki_card_app.security import add_security_headers, prepare_csrf_token, set_csrf_cookie
from anki_card_app.web import router as web_router


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Anki Card App",
        version="0.1.0",
        debug=settings.debug,
    )

    @application.middleware("http")
    async def security_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        new_csrf_cookie = prepare_csrf_token(request)
        response = await call_next(request)
        add_security_headers(response)
        if new_csrf_cookie is not None and "text/html" in response.headers.get("content-type", ""):
            set_csrf_cookie(response, new_csrf_cookie)
        return response

    static_directory = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=static_directory), name="static")
    application.include_router(auth_router)
    application.include_router(web_router)
    application.include_router(imports_router)
    application.include_router(notes_router)
    application.include_router(exports_router)

    @application.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            static_directory / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @application.get("/service-worker.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            static_directory / "service-worker.js",
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/",
            },
        )

    @application.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.app_env}

    @application.get("/ready", tags=["system"], response_model=None)
    def readiness() -> Response:
        if database_is_ready():
            return JSONResponse({"status": "ready"})
        return JSONResponse(
            {"status": "unavailable"},
            status_code=503,
        )

    return application
