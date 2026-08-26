import uuid

from sqlalchemy.orm import Session

from anki_card_app.user_service import ensure_user


def test_ensure_user_is_idempotent(db_session: Session) -> None:
    user_id = uuid.uuid4()

    created = ensure_user(db_session, user_id=user_id, email="learner@example.com")
    existing = ensure_user(db_session, user_id=user_id, email="ignored@example.com")

    assert created is existing
    assert existing.email == "learner@example.com"
    assert existing.timezone == "America/Los_Angeles"
