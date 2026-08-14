from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.card_service import CardContent, create_draft
from anki_card_app.config import get_settings
from anki_card_app.export_service import build_user_export
from anki_card_app.models import Card, CardState, CardType, UserAccount


def _draft_backup(db_session: Session) -> dict[str, Any]:
    source_user = UserAccount(email="backup-owner@example.com", timezone="Europe/London")
    db_session.add(source_user)
    db_session.flush()
    create_draft(
        db_session,
        user_id=source_user.id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Restored question", back="Restored answer"),
        created_by="ai",
    )
    db_session.commit()
    return build_user_export(db_session, user_id=source_user.id)


def test_restore_page_imports_backup_into_authenticated_empty_account(
    client: TestClient,
    db_session: Session,
) -> None:
    payload = _draft_backup(db_session)

    response = client.post(
        "/restore",
        files={"upload": ("backup.json", json.dumps(payload).encode(), "application/json")},
        data={"confirm_restore": "yes"},
    )

    restored_user_id = get_settings().development_user_id
    restored_card = db_session.scalar(select(Card).where(Card.user_id == restored_user_id))
    assert response.status_code == 200
    assert response.url.path == "/restore"
    assert "Restored 1 cards" in response.text
    assert "View drafts" in response.text
    assert restored_card is not None
    assert restored_card.state is CardState.DRAFT


def test_restore_page_rejects_second_restore_without_duplicates(
    client: TestClient,
    db_session: Session,
) -> None:
    payload = _draft_backup(db_session)
    upload = ("backup.json", json.dumps(payload).encode(), "application/json")
    first = client.post("/restore", files={"upload": upload}, data={"confirm_restore": "yes"})

    second = client.post("/restore", files={"upload": upload}, data={"confirm_restore": "yes"})

    restored_user_id = get_settings().development_user_id
    assert first.status_code == 200
    assert second.status_code == 422
    assert "Restore requires an empty account" in second.text
    assert (
        db_session.scalar(
            select(func.count()).select_from(Card).where(Card.user_id == restored_user_id)
        )
        == 1
    )


def test_restore_page_requires_confirmation_and_valid_json(client: TestClient) -> None:
    missing_confirmation = client.post(
        "/restore",
        files={"upload": ("backup.json", b"{}", "application/json")},
    )
    invalid_json = client.post(
        "/restore",
        files={"upload": ("backup.json", b"not-json", "application/json")},
        data={"confirm_restore": "yes"},
    )

    assert missing_confirmation.status_code == 422
    assert "Confirm that this account is empty" in missing_confirmation.text
    assert invalid_json.status_code == 422
    assert "Backup contains invalid JSON" in invalid_json.text


def test_restore_page_rolls_back_database_constraint_failure(
    client: TestClient,
    db_session: Session,
) -> None:
    payload = _draft_backup(db_session)
    source_id = payload["user"]["id"]
    payload["data"]["source_documents"] = [
        {
            "id": "2b9832e8-8f48-4b9f-b7b1-a70f8fe562e8",
            "user_id": source_id,
            "relative_path": "broken.md",
            "filename": "broken.md",
            "content_hash": "b" * 64,
            "raw_content": "Broken source",
            "source_modified_at": None,
            "imported_at": "2026-08-13T12:00:00+00:00",
        }
    ]
    payload["data"]["source_chunks"] = [
        {
            "id": "7b8b1de6-f9a1-42eb-9064-565c568ed299",
            "source_document_id": "2b9832e8-8f48-4b9f-b7b1-a70f8fe562e8",
            "sequence": -1,
            "heading_path": None,
            "text": "Invalid sequence",
            "token_estimate": 2,
            "created_at": "2026-08-13T12:00:00+00:00",
        }
    ]

    response = client.post(
        "/restore",
        files={"upload": ("backup.json", json.dumps(payload).encode(), "application/json")},
        data={"confirm_restore": "yes"},
    )

    restored_user_id = get_settings().development_user_id
    assert response.status_code == 422
    assert "conflicts with the target database" in response.text
    assert (
        db_session.scalar(
            select(func.count()).select_from(Card).where(Card.user_id == restored_user_id)
        )
        == 0
    )
