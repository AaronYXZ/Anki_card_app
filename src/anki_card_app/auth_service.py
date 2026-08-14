from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from anki_card_app.models import AuthSession, UserAccount, utc_now

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
MINIMUM_PASSWORD_LENGTH = 12


class AccountError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreatedSession:
    token: str
    expires_at: datetime


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if not normalized or "@" not in normalized or len(normalized) > 320:
        raise AccountError("Enter a valid email address.")
    return normalized


def hash_password(password: str) -> str:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise AccountError(f"Password must contain at least {MINIMUM_PASSWORD_LENGTH} characters.")
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(derived).decode(),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode(),
            salt=base64.urlsafe_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=KEY_BYTES,
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(derived).decode(), expected)
    except (ValueError, TypeError):
        return False


_DUMMY_PASSWORD_HASH = hash_password("authentication-timing-placeholder")


def create_account(session: Session, *, email: str, password: str) -> UserAccount:
    normalized_email = normalize_email(email)
    if session.scalar(select(UserAccount.id).where(UserAccount.email == normalized_email)):
        raise AccountError("An account with that email already exists.")
    account = UserAccount(
        email=normalized_email,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(account)
    session.flush()
    return account


def set_password(session: Session, *, email: str, password: str) -> UserAccount:
    account = session.scalar(select(UserAccount).where(UserAccount.email == normalize_email(email)))
    if account is None:
        raise AccountError("Account not found.")
    account.password_hash = hash_password(password)
    revoke_all_sessions(session, user_id=account.id)
    session.flush()
    return account


def authenticate(session: Session, *, email: str, password: str) -> UserAccount | None:
    try:
        normalized_email = normalize_email(email)
    except AccountError:
        return None
    account = session.scalar(select(UserAccount).where(UserAccount.email == normalized_email))
    encoded = account.password_hash if account is not None else None
    password_matches = verify_password(password, encoded or _DUMMY_PASSWORD_HASH)
    if account is None or not account.is_active or encoded is None or not password_matches:
        return None
    return account


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    session: Session,
    *,
    user_id: uuid.UUID,
    lifetime: timedelta,
    now: datetime | None = None,
) -> CreatedSession:
    current_time = now or utc_now()
    token = secrets.token_urlsafe(32)
    expires_at = current_time + lifetime
    session.add(
        AuthSession(
            user_id=user_id,
            token_digest=_token_digest(token),
            expires_at=expires_at,
        )
    )
    session.flush()
    return CreatedSession(token=token, expires_at=expires_at)


def resolve_session(
    session: Session, *, token: str, now: datetime | None = None
) -> UserAccount | None:
    if not token:
        return None
    current_time = now or utc_now()
    row = session.execute(
        select(AuthSession, UserAccount)
        .join(UserAccount, UserAccount.id == AuthSession.user_id)
        .where(
            AuthSession.token_digest == _token_digest(token),
            AuthSession.revoked_at.is_(None),
            UserAccount.is_active.is_(True),
        )
    ).one_or_none()
    if row is None:
        return None
    auth_session, account = row
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return account if expires_at > current_time else None


def revoke_session(session: Session, *, token: str) -> None:
    if token:
        session.execute(
            update(AuthSession)
            .where(AuthSession.token_digest == _token_digest(token))
            .values(revoked_at=utc_now())
        )


def revoke_all_sessions(session: Session, *, user_id: uuid.UUID) -> None:
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
