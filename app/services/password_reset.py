"""One-time password recovery with database-backed limits and atomic consumption."""

import hashlib
import hmac
import logging
import math
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.db.models import PasswordResetChallenge, PasswordResetRateLimit, User
from app.services import email_delivery
from app.services.auth import get_user_by_email, normalise_email


CODE_TTL_SECONDS = 600
RESEND_COOLDOWN_SECONDS = 60
MAX_CODE_ATTEMPTS = 5
MAX_EMAIL_REQUESTS = 5
MAX_IP_REQUESTS = 20
MAX_IP_VERIFICATIONS = 60
RATE_WINDOW_SECONDS = 3600
logger = logging.getLogger(__name__)


class PasswordResetError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class IssuedReset:
    challenge_id: uuid.UUID
    email: str | None
    code: str
    language: str


@dataclass(frozen=True)
class CompletedReset:
    email: str
    language: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest(purpose: str, value: str) -> str:
    return hmac.new(
        settings.jwt_secret_key.encode(),
        f"respiracare:password-reset:v1:{purpose}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _code_digest(challenge_id: uuid.UUID, code: str) -> str:
    return _digest("code", f"{challenge_id}:{code}")


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _record_limits(db: Session, specs: list[tuple[str, str, int, int]], now: datetime) -> None:
    # A consistent lock order avoids deadlocks between simultaneous requests.
    buckets = sorted((_digest(scope, value), limit, cooldown) for scope, value, limit, cooldown in specs)
    for key, limit, cooldown in buckets:
        db.execute(
            insert(PasswordResetRateLimit).values(
                key=key, window_start=now, next_allowed_at=now, request_count=0,
            ).on_conflict_do_nothing(index_elements=["key"])
        )
        bucket = db.scalar(
            select(PasswordResetRateLimit).where(PasswordResetRateLimit.key == key).with_for_update()
        )
        window_end = bucket.window_start + timedelta(seconds=RATE_WINDOW_SECONDS)
        if now >= window_end:
            bucket.window_start = now
            bucket.request_count = 0
            bucket.next_allowed_at = now
            window_end = now + timedelta(seconds=RATE_WINDOW_SECONDS)

        if bucket.request_count >= limit or bucket.next_allowed_at > now:
            retry_at = window_end if bucket.request_count >= limit else bucket.next_allowed_at
            raise PasswordResetError(
                "reset_rate_limited", "Please wait before trying password recovery again.",
                status=429, retry_after=max(1, math.ceil((retry_at - now).total_seconds())),
            )

        bucket.request_count += 1
        bucket.next_allowed_at = now + timedelta(seconds=cooldown)


def request_password_reset(db: Session, email: str, language: str, client_host: str) -> IssuedReset:
    now = utcnow()
    email = normalise_email(email)
    email_key = _digest("email", email)

    try:
        _record_limits(db, [
            ("request-email", email, MAX_EMAIL_REQUESTS, RESEND_COOLDOWN_SECONDS),
            ("request-ip", client_host, MAX_IP_REQUESTS, 0),
        ], now)
    except PasswordResetError:
        db.rollback()
        raise

    challenge = db.scalar(
        select(PasswordResetChallenge).where(
            PasswordResetChallenge.email_key == email_key,
        ).with_for_update()
    )
    user = get_user_by_email(db, email)
    challenge_id = uuid.uuid4()
    code = _new_code()
    values = {
        "id": challenge_id,
        "user_id": user.id if user else None,
        "code_digest": _code_digest(challenge_id, code),
        "auth_version": user.auth_version if user else None,
        "language": language,
        "requested_at": now,
        "expires_at": now + timedelta(seconds=CODE_TTL_SECONDS),
        "attempts": 0,
        "consumed_at": None,
    }
    if challenge is None:
        db.add(PasswordResetChallenge(email_key=email_key, **values))
    else:
        for key, value in values.items():
            setattr(challenge, key, value)
    recipient = user.email if user else None
    db.commit()

    # Unknown addresses follow the same response, storage and rate-limit path.
    return IssuedReset(challenge_id, recipient, code, language)


def _reject(db: Session, code: str) -> None:
    db.commit()  # Persist failed guesses and rate counters before rejecting.
    messages = {
        "invalid_code": "The code is invalid or has already been used.",
        "expired_code": "The code has expired. Request a new code.",
        "attempts_exhausted": "Too many incorrect codes. Request a new code.",
    }
    raise PasswordResetError(code, messages[code])


def complete_password_reset(
    db: Session, challenge_id: uuid.UUID, code: str, new_password: str, client_host: str,
) -> CompletedReset:
    now = utcnow()
    try:
        _record_limits(db, [("verify-ip", client_host, MAX_IP_VERIFICATIONS, 0)], now)
    except PasswordResetError:
        db.rollback()
        raise

    challenge = db.scalar(
        select(PasswordResetChallenge).where(
            PasswordResetChallenge.id == challenge_id,
        ).with_for_update()
    )
    if challenge is None or challenge.consumed_at is not None:
        _reject(db, "invalid_code")
    if challenge.expires_at <= now:
        challenge.consumed_at = now
        challenge.code_digest = None
        _reject(db, "expired_code")
    if challenge.attempts >= MAX_CODE_ATTEMPTS:
        _reject(db, "attempts_exhausted")

    if not hmac.compare_digest(challenge.code_digest or "", _code_digest(challenge_id, code)):
        challenge.attempts += 1
        exhausted = challenge.attempts >= MAX_CODE_ATTEMPTS
        if exhausted:
            challenge.code_digest = None
        _reject(db, "attempts_exhausted" if exhausted else "invalid_code")

    user = db.scalar(select(User).where(User.id == challenge.user_id).with_for_update())
    if user is None or challenge.auth_version != user.auth_version:
        challenge.consumed_at = now
        challenge.code_digest = None
        _reject(db, "invalid_code")

    user.password_hash = hash_password(new_password)
    user.auth_version += 1
    challenge.consumed_at = now
    challenge.code_digest = None
    result = CompletedReset(user.email, challenge.language)
    db.commit()
    return result


def deliver_reset_code(issued: IssuedReset) -> None:
    if issued.email is None:
        return
    with SessionLocal() as db:
        challenge = db.scalar(select(PasswordResetChallenge).where(
            PasswordResetChallenge.id == issued.challenge_id,
        ))
        if (
            challenge is None or challenge.consumed_at is not None
            or challenge.expires_at <= utcnow() or not challenge.code_digest
        ):
            return
    try:
        email_delivery.send_password_reset_code(issued.email, issued.code, issued.language)
    except (OSError, email_delivery.EmailDeliveryUnavailable):
        logger.error("Password reset email delivery failed.")
        with SessionLocal() as db:
            db.execute(update(PasswordResetChallenge).where(
                PasswordResetChallenge.id == issued.challenge_id,
            ).values(code_digest=None, consumed_at=utcnow()))
            db.commit()


def deliver_password_changed_notice(completed: CompletedReset) -> None:
    try:
        email_delivery.send_password_changed_notice(completed.email, completed.language)
    except (OSError, email_delivery.EmailDeliveryUnavailable):
        logger.error("Password change notification delivery failed.")
