from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.card_service import CardContent, create_draft
from anki_card_app.config import get_settings
from anki_card_app.export_service import build_user_export
from anki_card_app.models import Card, CardType, ReviewSession, UserAccount


def test_authenticated_backup_contains_complete_owned_history(
    client: TestClient,
    db_session: Session,
) -> None:
    client.get("/")
    owner_id = get_settings().development_user_id
    other_user = UserAccount(email="other@example.com")
    db_session.add(other_user)
    db_session.flush()
    create_draft(
        db_session,
        user_id=other_user.id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Other user's secret", back="Never export this"),
    )
    db_session.commit()

    client.post(
        "/imports/new",
        files={
            "upload": (
                "owned.md",
                b"# Owned source\nA private source snapshot.",
                "text/markdown",
            )
        },
    )
    client.post(
        "/cards/new",
        data={
            "card_type": "normal",
            "front": "Owned question",
            "back": "Owned answer",
        },
    )
    card = db_session.scalar(select(Card).where(Card.user_id == owner_id))
    assert card is not None
    client.post(f"/cards/{card.id}/approve")
    card.is_favorite = True
    card.favorited_at = card.updated_at
    db_session.commit()
    client.get("/review")
    review_session = db_session.scalar(
        select(ReviewSession).where(ReviewSession.user_id == owner_id)
    )
    assert review_session is not None
    client.post(f"/review/{review_session.id}/{card.id}/reveal")
    revealed = client.get("/review")
    attempt = re.search(r'name="attempt_id" value="([^"]+)"', revealed.text)
    assert attempt is not None
    client.post(
        f"/review/{review_session.id}/{card.id}/rate",
        data={"rating": "3", "attempt_id": attempt.group(1)},
    )

    response = client.get("/exports/backup.json")
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert payload["format"] == "anki-card-app-backup"
    assert payload["format_version"] == 2
    assert "study_notes" in payload["data"]
    assert payload["user"]["id"] == str(owner_id)
    assert payload["data"]["cards"][0]["is_favorite"] is True
    assert payload["data"]["cards"][0]["favorited_at"] is not None
    for table in (
        "source_documents",
        "source_chunks",
        "generation_runs",
        "generation_chunk_runs",
        "cards",
        "card_versions",
        "scheduling_states",
        "review_sessions",
        "review_session_cards",
        "review_logs",
    ):
        assert payload["data"][table]
    serialized = response.text
    assert "Owned question" in serialized
    assert "A private source snapshot" in serialized
    assert "Other user's secret" not in serialized
    assert "password_hash" not in serialized
    assert "token_digest" not in serialized


def test_export_rejects_unknown_user(db_session: Session) -> None:
    with pytest.raises(LookupError, match="User not found"):
        build_user_export(db_session, user_id=uuid.uuid4())
