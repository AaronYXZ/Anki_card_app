from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from anki_card_app.config import get_settings
from anki_card_app.imports_web import router as imports_router
from anki_card_app.notes_web import router as notes_router
from anki_card_app.web import router as web_router


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Anki Card App",
        version="0.1.0",
        debug=settings.debug,
    )
    static_directory = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=static_directory), name="static")
    application.include_router(web_router)
    application.include_router(imports_router)
    application.include_router(notes_router)

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

    return application
