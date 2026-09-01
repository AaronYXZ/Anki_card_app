from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import anki_card_app.app as app_module
from anki_card_app.app import create_app
from anki_card_app.main import app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}


def test_readiness_endpoint_reports_database_state(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "database_is_ready", lambda: True)
    ready = TestClient(create_app()).get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    monkeypatch.setattr(app_module, "database_is_ready", lambda: False)
    unavailable = TestClient(create_app()).get("/ready")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"status": "unavailable"}


def test_asgi_application_is_configured() -> None:
    assert app.title == "Anki Card App"
    assert app.version == "0.1.0"


def test_pwa_resources_are_served_from_root_scope() -> None:
    client = TestClient(create_app())

    manifest = client.get("/manifest.webmanifest")
    service_worker = client.get("/service-worker.js")
    styles = client.get("/static/app.css")

    assert manifest.status_code == 200
    assert "application/manifest+json" in manifest.headers["content-type"]
    assert manifest.json()["display"] == "standalone"
    assert [icon["sizes"] for icon in manifest.json()["icons"]] == ["192x192", "512x512"]
    assert service_worker.status_code == 200
    assert service_worker.headers["service-worker-allowed"] == "/"
    assert "no-cache" in service_worker.headers["cache-control"]
    assert 'CACHE_NAME = "anki-shell-v15"' in service_worker.text
    assert '"/static/app.css?v=15"' in service_worker.text
    assert '"/static/app.js?v=15"' in service_worker.text
    assert 'request.method !== "GET"' in service_worker.text
    assert 'caches.match("/static/offline.html")' in service_worker.text
    assert styles.status_code == 200
    assert ".skeleton-prompt { font-weight: 400; }" in styles.text
    assert ".highlight .k" in styles.text
    assert ".draft-card .markdown-content pre" in styles.text
    assert "white-space: pre-wrap" in styles.text
    assert ".draft-card .markdown-content table" in styles.text
    assert "table-layout: fixed" in styles.text
    assert ".approved-card .markdown-content pre" in styles.text
    assert ".approved-card .markdown-content table" in styles.text
    assert ".math.block" in styles.text
    assert ".favorite-button.active" in styles.text
    assert ".favorite-nav-link" in styles.text
    assert ".nav-dropdown .nav-button" in styles.text
    assert "grid-template-columns: minmax(0, 1fr)" in styles.text
    assert "flex-direction: row" in styles.text
    assert "justify-content: space-between" in styles.text


def test_pwa_icons_are_valid_png_resources() -> None:
    client = TestClient(create_app())

    small_icon = client.get("/static/icon-192.png")
    large_icon = client.get("/static/icon-512.png")

    assert small_icon.status_code == 200
    assert small_icon.headers["content-type"] == "image/png"
    assert small_icon.content.startswith(b"\x89PNG")
    assert large_icon.status_code == 200
    assert large_icon.headers["content-type"] == "image/png"
    assert large_icon.content.startswith(b"\x89PNG")
