from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from anki_card_app.auth_service import (
    AccountError,
    authenticate,
    create_account,
    create_session,
    hash_password,
    resolve_session,
    revoke_session,
    set_password,
    verify_password,
)
from anki_card_app.models import utc_now


def test_password_hashing_and_account_validation(db_session: Session) -> None:
    encoded = hash_password("a-long-test-password")

    assert verify_password("a-long-test-password", encoded)
    assert not verify_password("wrong-password", encoded)
    assert not verify_password("anything", "malformed")
    with pytest.raises(AccountError, match="at least 12"):
        hash_password("short")

    account = create_account(
        db_session,
        email="  OWNER@Example.COM ",
        password="a-long-test-password",
    )
    assert account.email == "owner@example.com"
    assert (
        authenticate(db_session, email="owner@example.com", password="a-long-test-password")
        == account
    )
    assert authenticate(db_session, email="owner@example.com", password="wrong") is None
    with pytest.raises(AccountError, match="already exists"):
        create_account(
            db_session,
            email="owner@example.com",
            password="another-long-password",
        )


def test_server_session_expiry_revocation_and_password_rotation(db_session: Session) -> None:
    account = create_account(
        db_session,
        email="owner@example.com",
        password="a-long-test-password",
    )
    now = utc_now()
    active = create_session(
        db_session,
        user_id=account.id,
        lifetime=timedelta(days=1),
        now=now,
    )
    expired = create_session(
        db_session,
        user_id=account.id,
        lifetime=timedelta(seconds=-1),
        now=now,
    )

    assert resolve_session(db_session, token=active.token, now=now) == account
    assert resolve_session(db_session, token=expired.token, now=now) is None
    assert resolve_session(db_session, token="unknown", now=now) is None

    revoke_session(db_session, token=active.token)
    assert resolve_session(db_session, token=active.token, now=now) is None

    replacement = create_session(
        db_session,
        user_id=account.id,
        lifetime=timedelta(days=1),
        now=now,
    )
    set_password(
        db_session,
        email=account.email,
        password="replacement-password",
    )
    assert resolve_session(db_session, token=replacement.token, now=now) is None
    assert authenticate(db_session, email=account.email, password="replacement-password") == account
