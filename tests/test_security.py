from __future__ import annotations

import re
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from starlette.responses import Response

import anki_card_app.security as security


def test_csrf_cookie_hidden_field_and_validation(test_app: FastAPI) -> None:
    with TestClient(test_app) as client:
        form = client.get("/cards/new")
        token_match = re.search(r'name="csrf_token" value="([^"]+)"', form.text)
        assert token_match is not None
        token = token_match.group(1)
        assert client.cookies.get("anki_csrf") == token

        missing = client.post(
            "/cards/new",
            data={"card_type": "normal", "front": "Question", "back": "Answer"},
        )
        invalid = client.post(
            "/cards/new",
            data={
                "csrf_token": "wrong-token",
                "card_type": "normal",
                "front": "Question",
                "back": "Answer",
            },
        )
        valid = client.post(
            "/cards/new",
            data={
                "csrf_token": token,
                "card_type": "normal",
                "front": "Question",
                "back": "Answer",
            },
            follow_redirects=False,
        )

        assert missing.status_code == 403
        assert invalid.status_code == 403
        assert valid.status_code == 303


def test_security_headers_are_applied(client: TestClient) -> None:
    response = client.get("/")

    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_production_security_headers_include_hsts(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(app_env="production"),
    )
    response = Response()

    security.add_security_headers(response)

    assert response.headers["strict-transport-security"] == ("max-age=31536000; includeSubDomains")


def test_user_content_is_rendered_as_escaped_text(client: TestClient) -> None:
    created = client.post(
        "/cards/new",
        data={
            "card_type": "normal",
            "front": "<script>alert('front')</script>",
            "back": '<img src=x onerror="alert(1)">',
        },
    )
    assert created.status_code == 200

    page = client.get("/cards/drafts")
    assert "<script>" not in page.text
    assert "<img src=x" not in page.text
    assert "&lt;script&gt;alert" in page.text
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in page.text
