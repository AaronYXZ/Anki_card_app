import re
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anki_card_app.card_service import CardContent, create_draft
from anki_card_app.config import get_settings
from anki_card_app.models import (
    Card,
    CardState,
    CardType,
    CardVersion,
    ReviewLog,
    ReviewSession,
    SchedulingState,
    SourceChunk,
    SourceDocument,
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
    assert 'id="top"' in drafts.text
    assert 'class="back-to-top"' in drafts.text
    assert 'href="#top"' in drafts.text
    assert "Nothing is due" in review.text
    assert "Create a card" in new_card.text
    assert "Skeleton Recall" in new_card.text
    assert install.status_code == 200
    assert "Add to Home Screen" in install.text
    assert "Online connection required" in install.text
    assert "/manifest.webmanifest" in install.text
    assert "/static/app.js" in install.text


def test_primary_navigation_is_grouped_into_four_categories(client: TestClient) -> None:
    page = client.get("/")

    assert page.text.count('class="nav-group"') == 3
    assert page.text.count('name="primary-nav-group"') == 3
    assert "<summary>Create</summary>" in page.text
    assert '<a href="/imports">Import</a>' in page.text
    assert '<a href="/cards/new">New card</a>' in page.text
    assert '<a href="/notes">Imported notes</a>' in page.text
    assert '<a class="nav-link" href="/review">Review</a>' in page.text
    assert "<summary>Modify</summary>" in page.text
    assert '<a href="/cards/drafts">Drafts</a>' in page.text
    assert '<a href="/cards">Cards</a>' in page.text
    assert "<summary>Utils</summary>" in page.text
    assert '<a href="/exports/backup.json">Export</a>' in page.text
    assert '<a href="/install">Install</a>' in page.text
    assert '<a href="/restore">Restore</a>' in page.text
    utils_start = page.text.index("<summary>Utils</summary>")
    utils_end = page.text.index("</details>", utils_start)
    assert '<form action="/logout" method="post">' in page.text[utils_start:utils_end]
    assert '<a class="nav-link favorite-nav-link" href="/favorites"' in page.text
    assert 'aria-label="Favorites"' in page.text
    assert ">Sign out</button>" in page.text
    assert page.text.index(">Sign out</button>") < utils_end


def test_favorites_page_is_user_scoped_and_newest_first(
    client: TestClient,
    db_session: Session,
) -> None:
    client.get("/")
    owner_id = get_settings().development_user_id
    older = create_draft(
        db_session,
        user_id=owner_id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Older favorite", back="Older answer"),
    )
    newer = create_draft(
        db_session,
        user_id=owner_id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Newer favorite", back="Newer answer"),
    )
    hidden = create_draft(
        db_session,
        user_id=owner_id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Not a favorite", back="Hidden answer"),
    )
    other_user = UserAccount(email="favorite-other@example.com")
    db_session.add(other_user)
    db_session.flush()
    other = create_draft(
        db_session,
        user_id=other_user.id,
        card_type=CardType.NORMAL,
        content=CardContent(front="Other user's favorite", back="Private"),
    )
    older.is_favorite = True
    older.favorited_at = datetime(2026, 8, 26, 12, tzinfo=UTC)
    newer.is_favorite = True
    newer.favorited_at = datetime(2026, 8, 27, 12, tzinfo=UTC)
    other.is_favorite = True
    other.favorited_at = datetime(2026, 8, 27, 13, tzinfo=UTC)
    db_session.commit()

    page = client.get("/favorites")

    assert page.status_code == 200
    assert "Favorites ❤️" in page.text
    assert page.text.index("Newer favorite") < page.text.index("Older favorite")
    assert "Not a favorite" not in page.text
    assert "Other user's favorite" not in page.text
    assert f'href="/cards/{newer.id}"' in page.text
    assert f'href="/cards/{newer.id}/edit"' in page.text
    assert hidden.is_favorite is False


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
    assert 'aria-label="Add to favorites"' in revealed_page.text
    assert 'aria-pressed="false"' in revealed_page.text

    favorited = client.post(
        f"/cards/{card.id}/favorite",
        data={"favorite": "true"},
        follow_redirects=False,
    )
    assert favorited.status_code == 303
    assert favorited.headers["location"] == "/review"
    db_session.refresh(card)
    assert card.is_favorite is True

    favorite_page = client.get("/review")
    assert 'class="favorite-button active"' in favorite_page.text
    assert 'aria-label="Remove from favorites"' in favorite_page.text
    assert 'aria-pressed="true"' in favorite_page.text
    assert "Again" in favorite_page.text
    attempt_match = re.search(r'name="attempt_id" value="([^"]+)"', favorite_page.text)
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


def test_approved_cards_page_lists_active_cards_only(
    client: TestClient,
    db_session: Session,
) -> None:
    client.post(
        "/cards/new",
        data={"card_type": "normal", "front": "Approved question", "back": "Answer"},
    )
    approved = db_session.scalar(select(Card).where(Card.state == CardState.DRAFT))
    assert approved is not None
    client.post(f"/cards/{approved.id}/approve")
    client.post(
        "/cards/new",
        data={"card_type": "normal", "front": "Still a draft", "back": "Hidden"},
    )

    page = client.get("/cards")

    assert page.status_code == 200
    assert "Approved question" in page.text
    assert "Still a draft" not in page.text
    assert "Created " in page.text
    assert "Version 1" in page.text
    assert 'class="source-card approved-card"' in page.text
    assert 'class="back-to-top" href="#top"' in page.text
    assert f'href="/cards/{approved.id}"' in page.text
    assert f'href="/cards/{approved.id}/edit"' in page.text


def test_active_card_edit_redirects_to_review_preview_without_review_side_effects(
    client: TestClient,
    db_session: Session,
) -> None:
    client.post(
        "/cards/new",
        data={"card_type": "normal", "front": "Original", "back": "Old answer"},
    )
    card = db_session.scalar(select(Card))
    assert card is not None
    client.post(f"/cards/{card.id}/approve")

    edited = client.post(
        f"/cards/{card.id}/edit",
        data={
            "front": "Updated **question**",
            "back": "Updated answer with `code`.",
        },
        follow_redirects=False,
    )

    assert edited.status_code == 303
    assert edited.headers["location"] == f"/cards/{card.id}"
    preview = client.get(edited.headers["location"])
    assert preview.status_code == 200
    assert "Review preview" in preview.text
    assert "Modified card" in preview.text
    assert "Updated <strong>question</strong>" in preview.text
    assert "Updated answer with <code>code</code>." in preview.text
    assert "<details open>" in preview.text
    assert "does not change the card's schedule or review history" in preview.text
    assert db_session.scalar(select(func.count()).select_from(ReviewSession)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewLog)) == 0


def test_manual_markdown_is_preserved_and_rendered_in_draft_and_review(
    client: TestClient,
    db_session: Session,
) -> None:
    front = "## Power\n\nWhy is **power** useful with `beta`?"
    back = "It helps detect:\n\n- **real effects**\n- meaningful differences"
    created = client.post(
        "/cards/new",
        data={"card_type": "normal", "front": front, "back": back},
        follow_redirects=False,
    )

    assert created.status_code == 303
    card = db_session.scalar(select(Card))
    assert card is not None
    version = db_session.get(CardVersion, card.current_version_id)
    assert version is not None
    assert version.front == front
    assert version.back == back

    inbox = client.get("/cards/drafts")
    assert "<h2>Power</h2>" in inbox.text
    assert "Why is <strong>power</strong> useful with <code>beta</code>?" in inbox.text
    assert "<ul>" in inbox.text
    assert "<strong>real effects</strong>" in inbox.text

    client.post(f"/cards/{card.id}/approve", follow_redirects=False)
    review = client.get("/review")
    assert "<h2>Power</h2>" in review.text
    assert "<strong>power</strong>" in review.text


def test_math_is_rendered_in_drafts_and_approved_cards(
    client: TestClient, db_session: Session
) -> None:
    formula = r"Y_{adj} = Y - \theta \cdot (X - \bar{X})"
    created = client.post(
        "/cards/new",
        data={
            "card_type": "normal",
            "front": formula,
            "back": r"Use $\bar{X}$ as the mean.",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    card = db_session.scalar(select(Card))
    assert card is not None

    draft = client.get("/cards/drafts")
    assert '<div class="math block"><math' in draft.text
    assert "<msub>" in draft.text
    assert "<mover>" in draft.text

    client.post(f"/cards/{card.id}/approve", follow_redirects=False)
    approved = client.get("/cards")
    assert '<div class="math block"><math' in approved.text
    assert '<span class="math inline"><math' in approved.text


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
    front = "Resolving **disagreement**\n\n1. Situation\n2. Conflict\n3. Action\n4. Result"
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
    assert "Resolving <strong>disagreement</strong>" in inbox.text
    assert "Proposed guarded rollout" in inbox.text
    assert 'class="markdown-content card-prompt skeleton-prompt"' in inbox.text
    approved = client.post(f"/cards/{card.id}/approve", follow_redirects=False)
    assert approved.status_code == 303

    review = client.get("/review")
    assert "Resolving <strong>disagreement</strong>" in review.text
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
    missing_preview = client.get(f"/cards/{missing_id}")
    missing_approve = client.post(f"/cards/{missing_id}/approve")
    missing_reject = client.post(f"/cards/{missing_id}/reject")

    assert invalid_type.status_code == 422
    assert "Choose Normal, Cloze, or Skeleton Recall" in invalid_type.text
    assert missing_edit.status_code == 404
    assert missing_preview.status_code == 404
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


def test_imported_drafts_follow_their_original_note_order(
    client: TestClient, db_session: Session
) -> None:
    client.get("/")
    user_id = get_settings().development_user_id
    document = SourceDocument(
        user_id=user_id,
        relative_path="ordered.md",
        filename="ordered.md",
        content_hash="a" * 64,
        raw_content="Paragraph one.\n\nParagraph two.\n\nParagraph three.",
    )
    db_session.add(document)
    db_session.flush()
    first_chunk = SourceChunk(
        source_document_id=document.id,
        sequence=0,
        text="Paragraph one.\n\nParagraph two.",
    )
    second_chunk = SourceChunk(
        source_document_id=document.id,
        sequence=1,
        text="Paragraph three.",
    )
    db_session.add_all([first_chunk, second_chunk])
    db_session.flush()

    for front, excerpt, chunk in (
        ("Question from paragraph three", "Paragraph three.", second_chunk),
        ("Question from paragraph two", "Paragraph two.", first_chunk),
        ("Question from paragraph one", "Paragraph one.", first_chunk),
    ):
        create_draft(
            db_session,
            user_id=user_id,
            card_type=CardType.NORMAL,
            content=CardContent(front=front, back="Answer"),
            source_document_id=document.id,
            source_chunk_id=chunk.id,
            source_excerpt=excerpt,
        )
    db_session.commit()

    page = client.get("/cards/drafts")

    assert page.text.index("Question from paragraph one") < page.text.index(
        "Question from paragraph two"
    )
    assert page.text.index("Question from paragraph two") < page.text.index(
        "Question from paragraph three"
    )


def test_development_user_is_created_once(client: TestClient, db_session: Session) -> None:
    client.get("/")
    client.get("/")

    settings = get_settings()
    user = db_session.get(UserAccount, settings.development_user_id)
    assert user is not None
