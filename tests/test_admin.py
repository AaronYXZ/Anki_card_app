from __future__ import annotations

import sys

from pytest import MonkeyPatch
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import anki_card_app.admin as admin
from anki_card_app.auth_service import authenticate
from anki_card_app.models import UserAccount


def test_admin_creates_user_and_rotates_password(
    test_engine: Engine,
    monkeypatch: MonkeyPatch,
    capsys: object,
) -> None:
    monkeypatch.setattr(admin, "get_engine", lambda: test_engine)
    passwords = iter(("initial-password", "initial-password"))
    monkeypatch.setattr(admin.getpass, "getpass", lambda prompt: next(passwords))
    monkeypatch.setattr(
        sys,
        "argv",
        ["anki-card-admin", "create-user", "--email", "Owner@Example.com"],
    )
    admin.main()

    with Session(test_engine) as session:
        account = session.scalar(select(UserAccount))
        assert account is not None
        assert account.email == "owner@example.com"
        assert authenticate(session, email=account.email, password="initial-password") == account

    passwords = iter(("replacement-password", "replacement-password"))
    monkeypatch.setattr(admin.getpass, "getpass", lambda prompt: next(passwords))
    monkeypatch.setattr(
        sys,
        "argv",
        ["anki-card-admin", "set-password", "--email", "owner@example.com"],
    )
    admin.main()

    with Session(test_engine) as session:
        assert (
            authenticate(session, email="owner@example.com", password="replacement-password")
            is not None
        )
