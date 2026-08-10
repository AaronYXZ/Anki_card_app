import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from anki_card_app.config import get_settings
from anki_card_app.models import (
    Card,
    CardState,
    CardType,
    CardVersion,
    ReviewSession,
    SchedulingState,
    UserAccount,
)


def test_dashboard_and_empty_workflows(client: TestClient) -> None:
    dashboard = client.get("/")
    drafts = client.get("/cards/drafts")
    review = client.get("/review")
    new_card = client.get("/cards/new")
    install = client.get("/install")

    assert dashboard.status_code == 200
    assert "0 cards are ready" in dashboard.text
    assert "30-day first-attempt recall" in dashboard.text
    assert "N/A" in dashboard.text
    assert "No drafts waiting" in drafts.text
    assert "Nothing is due" in review.text
    assert "Create a card" in new_card.text
    assert "Skeleton Recall" in new_card.text
    assert install.status_code == 200
    assert "Add to Home Screen" in install.text
    assert "Online connection required" in install.text
    assert "/manifest.webmanifest" in install.text
    assert "/static/app.js" in install.text


def test_normal_card_create_edit_approve_and_review(
    client: TestClient, db_session: Session
) -> None:
    invalid = client.post(
        "/cards/new",
        data={"card_type": "normal", "front": "Only a question"},
    )
    assert invalid.status_code == 422
    assert "require both a question and an answer" in invalid.text

    created = client.post(
        "/cards/new",
        data={
            "card_type": "normal",
            "front": "What is statistical power?",
            "back": "The probability of detecting a real effect.",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/cards/drafts"

    card = db_session.scalar(select(Card))
    assert card is not None
    inbox = client.get("/cards/drafts")
    assert "What is statistical power?" in inbox.text

    edit_form = client.get(f"/cards/{card.id}/edit")
    assert edit_form.status_code == 200
    assert "Versioned editing" in edit_form.text

    invalid_edit = client.post(f"/cards/{card.id}/edit", data={"front": "Missing answer"})
    assert invalid_edit.status_code == 422

    edited = client.post(
        f"/cards/{card.id}/edit",
        data={"front": "Define statistical power.", "back": "It equals one minus beta."},
        follow_redirects=False,
    )
    assert edited.status_code == 303
    assert edited.headers["location"] == f"/cards/drafts#card-{card.id}"
    versions = db_session.scalars(
        select(CardVersion)
        .where(CardVersion.card_id == card.id)
        .order_by(CardVersion.version_number)
    ).all()
    assert [version.version_number for version in versions] == [1, 2]

    approved = client.post(f"/cards/{card.id}/approve", follow_redirects=False)
    assert approved.status_code == 303
    db_session.refresh(card)
    assert card.state is CardState.ACTIVE
    assert db_session.get(SchedulingState, card.id) is not None

    review = client.get("/review")
    assert "Define statistical power." in review.text
    assert "It equals one minus beta." not in review.text
    assert 'data-shortcut="Space"' in review.text
    assert "<kbd>Space</kbd>" in review.text
    review_session = db_session.scalar(select(ReviewSession))
    assert review_session is not None

    revealed = client.post(
        f"/review/{review_session.id}/{card.id}/reveal",
        follow_redirects=False,
    )
    assert revealed.status_code == 303
    revealed_page = client.get("/review")
    assert "It equals one minus beta." in revealed_page.text
    assert "Again" in revealed_page.text
    assert 'data-shortcut="1"' in revealed_page.text
    assert 'data-shortcut="4"' in revealed_page.text
    attempt_match = re.search(r'name="attempt_id" value="([^"]+)"', revealed_page.text)
    assert attempt_match is not None

    rated = client.post(
        f"/review/{review_session.id}/{card.id}/rate",
        data={"rating": "3", "attempt_id": attempt_match.group(1)},
        follow_redirects=False,
    )
    assert rated.status_code == 303
    assert rated.headers["location"] == f"/review/sessions/{review_session.id}"
    summary = client.get(rated.headers["location"])
    assert "1 cards reviewed" in summary.text
    assert "Good" in summary.text


def test_cloze_card_rendering_and_rejection(client: TestClient, db_session: Session) -> None:
    created = client.post(
        "/cards/new",
        data={
            "card_type": "cloze",
            "cloze_text": "Power equals {{c1::one minus beta::formula}}.",
            "back_extra": "Beta is the Type II error rate.",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    card = db_session.scalar(select(Card).where(Card.card_type == CardType.CLOZE))
    assert card is not None

    inbox = client.get("/cards/drafts")
    assert "Power equals […]." in inbox.text
    assert "Power equals one minus beta." in inbox.text

    rejected = client.post(f"/cards/{card.id}/reject", follow_redirects=False)
    assert rejected.status_code == 303
    assert rejected.headers["location"] == "/cards/drafts"
    db_session.refresh(card)
    assert card.state is CardState.REJECTED
    assert "No drafts waiting" in client.get("/cards/drafts").text


def test_skeleton_recall_card_create_approve_and_review(
    client: TestClient, db_session: Session
) -> None:
    front = "Resolving disagreement\n\n1. Situation\n2. Conflict\n3. Action\n4. Result"
    back = (
        "1. Situation\n- Launch decision\n\n"
        "2. Conflict\n- Evidence was inconclusive\n\n"
        "3. Action\n- Proposed guarded rollout\n\n"
        "4. Result\n- Collected stronger evidence"
    )
    created = client.post(
        "/cards/new",
        data={"card_type": "skeleton_recall", "front": front, "back": back},
        follow_redirects=False,
    )
    assert created.status_code == 303
    card = db_session.scalar(select(Card).where(Card.card_type == CardType.SKELETON_RECALL))
    assert card is not None

    inbox = client.get("/cards/drafts")
    assert "Resolving disagreement" in inbox.text
    assert "Proposed guarded rollout" in inbox.text
    approved = client.post(f"/cards/{card.id}/approve", follow_redirects=False)
    assert approved.status_code == 303

    review = client.get("/review")
    assert "Resolving disagreement" in review.text
    assert "Proposed guarded rollout" not in review.text
    review_session = db_session.scalar(select(ReviewSession))
    assert review_session is not None
    client.post(
        f"/review/{review_session.id}/{card.id}/reveal",
        follow_redirects=False,
    )
    revealed = client.get("/review")
    assert "Proposed guarded rollout" in revealed.text


def test_invalid_card_type_and_missing_card_errors(client: TestClient) -> None:
    invalid_type = client.post(
        "/cards/new",
        data={"card_type": "unknown", "front": "Question", "back": "Answer"},
    )
    missing_id = uuid.uuid4()
    missing_edit = client.get(f"/cards/{missing_id}/edit")
    missing_approve = client.post(f"/cards/{missing_id}/approve")
    missing_reject = client.post(f"/cards/{missing_id}/reject")

    assert invalid_type.status_code == 422
    assert "Choose Normal, Cloze, or Skeleton Recall" in invalid_type.text
    assert missing_edit.status_code == 404
    assert missing_approve.status_code == 404
    assert missing_reject.status_code == 404


def test_draft_actions_redirect_to_adjacent_card(client: TestClient, db_session: Session) -> None:
    for number in range(3):
        response = client.post(
            "/cards/new",
            data={
                "card_type": "normal",
                "front": f"Question {number}",
                "back": f"Answer {number}",
            },
        )
        assert response.status_code == 200

    cards = db_session.scalars(
        select(Card).where(Card.state == CardState.DRAFT).order_by(Card.created_at.desc(), Card.id)
    ).all()
    assert len(cards) == 3

    approved = client.post(f"/cards/{cards[0].id}/approve", follow_redirects=False)
    assert approved.headers["location"] == f"/cards/drafts#card-{cards[1].id}"

    rejected = client.post(f"/cards/{cards[2].id}/reject", follow_redirects=False)
    assert rejected.headers["location"] == f"/cards/drafts#card-{cards[1].id}"
    page = client.get("/cards/drafts")
    assert f'id="card-{cards[1].id}"' in page.text


def test_development_user_is_created_once(client: TestClient, db_session: Session) -> None:
    client.get("/")
    client.get("/")

    settings = get_settings()
    user = db_session.get(UserAccount, settings.development_user_id)
    assert user is not None
