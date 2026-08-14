import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.card_service import CardContent, create_draft, reject_card
from anki_card_app.models import CardType, SourceChunk, SourceDocument


def test_note_ledger_tracks_source_runs_and_cards(
    client: TestClient,
    db_session: Session,
) -> None:
    client.post(
        "/imports/new",
        files={
            "upload": (
                "ml.md",
                b"# Bias and variance\nBias and variance describe model error.",
                "text/markdown",
            )
        },
    )
    document = db_session.scalar(select(SourceDocument))
    chunk = db_session.scalar(select(SourceChunk))
    assert document is not None
    assert chunk is not None
    draft = create_draft(
        db_session,
        user_id=document.user_id,
        card_type=CardType.NORMAL,
        content=CardContent(
            front="What is **bias**?",
            back="- Systematic model error\n- A persistent deviation",
        ),
        source_document_id=document.id,
        source_chunk_id=chunk.id,
        source_excerpt=chunk.text,
    )
    rejected = create_draft(
        db_session,
        user_id=document.user_id,
        card_type=CardType.NORMAL,
        content=CardContent(front="What is variance?", back="Sensitivity to training data."),
        source_document_id=document.id,
        source_chunk_id=chunk.id,
    )
    reject_card(db_session, user_id=document.user_id, card_id=rejected.id)
    db_session.commit()

    listing = client.get("/notes")
    detail = client.get(f"/notes/{document.id}")

    assert listing.status_code == 200
    assert "ml.md" in listing.text
    assert "2" in listing.text
    assert detail.status_code == 200
    assert "2" in detail.text
    assert "1</strong><span>awaiting review" in detail.text
    assert "1</strong><span>rejected" in detail.text
    assert "What is <strong>bias</strong>?" in detail.text
    assert "<li>Systematic model error</li>" in detail.text
    assert f"/cards/{draft.id}/edit" in detail.text
    assert "Generation history" in detail.text


def test_note_ledger_empty_and_missing(client: TestClient) -> None:
    empty = client.get("/notes")
    missing = client.get(f"/notes/{uuid.uuid4()}")

    assert empty.status_code == 200
    assert "No imported notes" in empty.text
    assert missing.status_code == 404
