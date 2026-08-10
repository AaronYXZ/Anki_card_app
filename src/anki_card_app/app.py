from __future__ import annotations

from fastapi import FastAPI

from anki_card_app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Anki Card App",
        version="0.1.0",
        debug=settings.debug,
    )

    @application.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.app_env}

    return application
