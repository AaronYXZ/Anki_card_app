from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session

import anki_card_app.auth as auth
import anki_card_app.auth_web as auth_web
import anki_card_app.security as security
from anki_card_app.auth_service import create_account
from anki_card_app.config import Settings
from anki_card_app.models import AuthSession, Card, GenerationRun, ReviewSession, SourceDocument
from anki_card_app.security import csrf_token_for_session


@pytest.fixture
def password_client(
    test_app: FastAPI,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> Iterator[TestClient]:
    settings = Settings(auth_mode="password", session_cookie_secure=False)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_web, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    create_account(
        db_session,
        email="first@example.com",
        password="first-test-password",
    )
    create_account(
        db_session,
        email="second@example.com",
        password="second-test-password",
    )
    db_session.commit()
    with TestClient(test_app) as client:
        csrf_token = "test_csrf_token_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        client.cookies.set(settings.csrf_cookie_name, csrf_token)
        client.headers["X-CSRF-Token"] = csrf_token
        yield client


def refresh_authenticated_csrf_header(client: TestClient) -> None:
    session_token = client.cookies.get("anki_session")
    assert session_token is not None
    client.headers["X-CSRF-Token"] = csrf_token_for_session(session_token)


def sign_in(client: TestClient, email: str, password: str) -> None:
    anonymous_token = client.cookies.get("anki_csrf")
    assert anonymous_token is not None
    client.headers["X-CSRF-Token"] = anonymous_token
    response = client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    refresh_authenticated_csrf_header(client)


def test_login_required_invalid_credentials_and_logout(
    password_client: TestClient,
    db_session: Session,
) -> None:
    anonymous = password_client.get("/cards/drafts?view=all", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login?next=/cards/drafts%3Fview%3Dall"

    login_page = password_client.get(anonymous.headers["location"])
    assert login_page.status_code == 200
    assert "Private alpha" in login_page.text
    assert 'value="/cards/drafts?view=all"' in login_page.text

    anonymous_upload = password_client.post(
        "/imports/new",
        data={"model": "untrusted-model"},
        files={"upload": ("private.md", b"# Private", "text/markdown")},
        follow_redirects=False,
    )
    assert anonymous_upload.status_code == 303
    assert anonymous_upload.headers["location"].startswith("/login?next=/imports/new")

    invalid = password_client.post(
        "/login",
        data={"email": "missing@example.com", "password": "not-the-password"},
    )
    assert invalid.status_code == 401
    assert "Invalid email or password" in invalid.text

    signed_in = password_client.post(
        "/login",
        data={
            "email": "FIRST@example.com",
            "password": "first-test-password",
            "next": "/cards/drafts",
        },
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/cards/drafts"
    cookie = signed_in.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    refresh_authenticated_csrf_header(password_client)
    assert "Sign out" in password_client.get("/").text

    auth_session = db_session.scalar(select(AuthSession))
    assert auth_session is not None
    assert auth_session.revoked_at is None
    logged_out = password_client.post("/logout", follow_redirects=False)
    assert logged_out.status_code == 303
    db_session.refresh(auth_session)
    assert auth_session.revoked_at is not None
    assert password_client.get("/", follow_redirects=False).status_code == 303


def test_login_rejects_external_redirect(password_client: TestClient) -> None:
    response = password_client.post(
        "/login",
        data={
            "email": "first@example.com",
            "password": "first-test-password",
            "next": "//attacker.example/path",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


def test_two_users_cannot_read_or_mutate_each_others_data(
    password_client: TestClient,
    db_session: Session,
) -> None:
    sign_in(password_client, "first@example.com", "first-test-password")
    password_client.post(
        "/cards/new",
        data={
            "card_type": "normal",
            "front": "First user's private question",
            "back": "First user's private answer",
        },
    )
    card = db_session.scalar(select(Card))
    assert card is not None
    password_client.post(f"/cards/{card.id}/approve")
    password_client.post(
        "/imports/new",
        files={"upload": ("private.md", b"# Private\nFirst user only", "text/markdown")},
    )
    document = db_session.scalar(select(SourceDocument))
    run = db_session.scalar(select(GenerationRun))
    assert document is not None
    assert run is not None
    password_client.get("/review")
    review_session = db_session.scalar(select(ReviewSession))
    assert review_session is not None

    password_client.post("/logout")
    sign_in(password_client, "second@example.com", "second-test-password")

    dashboard = password_client.get("/")
    assert "0 cards are ready" in dashboard.text
    assert "First user's private question" not in password_client.get("/cards/drafts").text
    assert "First user's private question" not in password_client.get("/cards").text
    assert "private.md" not in password_client.get("/notes").text
    assert "private.md" not in password_client.get("/imports").text

    assert password_client.get(f"/cards/{card.id}/edit").status_code == 404
    assert password_client.post(f"/cards/{card.id}/approve").status_code == 404
    assert password_client.post(f"/cards/{card.id}/reject").status_code == 404
    assert password_client.get(f"/notes/{document.id}").status_code == 404
    assert password_client.get(f"/imports/{run.id}").status_code == 404
    assert password_client.post(f"/imports/{run.id}/retry").status_code == 404
    assert password_client.get(f"/review/sessions/{review_session.id}").status_code == 404
    assert password_client.post(f"/review/{review_session.id}/{card.id}/reveal").status_code == 404
