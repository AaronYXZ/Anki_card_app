from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from anki_card_app.models import UserAccount


def ensure_user(session: Session, *, user_id: uuid.UUID, email: str) -> UserAccount:
    user = session.get(UserAccount, user_id)
    if user is not None:
        return user

    user = UserAccount(id=user_id, email=email)
    session.add(user)
    session.flush()
    return user
