from fastapi.testclient import TestClient

from anki_card_app.app import create_app
from anki_card_app.main import app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}


def test_asgi_application_is_configured() -> None:
    assert app.title == "Anki Card App"
    assert app.version == "0.1.0"
