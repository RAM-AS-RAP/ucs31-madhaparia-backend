import itertools
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, verify_password
from app.db.models import PasswordResetChallenge, User
from app.main import app
from app.services import email_delivery, password_reset


PASSWORD = "SyntheticOldPassword123!"
NEW_PASSWORD = "SyntheticNewPassword456!"


@pytest.fixture
def recovery(monkeypatch):
    outbox = []
    clock = [datetime.now(timezone.utc)]
    codes = itertools.count(100000)
    monkeypatch.setattr(email_delivery, "ensure_email_configured", lambda: None)
    monkeypatch.setattr(email_delivery, "_send_message", outbox.append)
    monkeypatch.setattr(password_reset, "utcnow", lambda: clock[0])
    monkeypatch.setattr(password_reset, "_new_code", lambda: str(next(codes)))
    return SimpleNamespace(
        client=TestClient(app, client=(f"reset-test-{uuid.uuid4().hex}", 50000)),
        outbox=outbox,
        clock=clock,
    )


def create_account(recovery):
    email = f"reset-{uuid.uuid4().hex}@example.com"
    result = recovery.client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert result.status_code == 201, result.text
    return email, uuid.UUID(result.json()["id"])


def request_code(recovery, email, language="en"):
    result = recovery.client.post("/auth/password-reset/request", json={"email": email, "language": language})
    assert result.status_code == 202, result.text
    code = re.search(r"\b[0-9]{6}\b", recovery.outbox[-1].get_content()).group()
    return result.json()["challenge_id"], code


def confirm(recovery, challenge_id, code, password=NEW_PASSWORD):
    return recovery.client.post("/auth/password-reset/confirm", json={
        "challenge_id": challenge_id, "code": code, "new_password": password,
    })


def test_reset_changes_password_consumes_code_and_revokes_existing_tokens(recovery):
    email, user_id = create_account(recovery)
    login = recovery.client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    challenge_id, code = request_code(recovery, email)

    result = confirm(recovery, challenge_id, code)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "password_reset"
    assert "access_token" not in result.json()
    assert NEW_PASSWORD not in result.text
    assert result.headers["cache-control"] == "no-store"

    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert verify_password(NEW_PASSWORD, user.password_hash)
        assert not verify_password(PASSWORD, user.password_hash)
        assert user.auth_version == 1
        challenge = db.scalar(select(PasswordResetChallenge).where(
            PasswordResetChallenge.id == uuid.UUID(challenge_id),
        ))
        assert challenge.consumed_at is not None
        assert challenge.code_digest is None

    assert recovery.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert confirm(recovery, challenge_id, code).status_code == 400
    assert recovery.client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401
    new_login = recovery.client.post("/auth/login", json={"email": email, "password": NEW_PASSWORD})
    assert new_login.status_code == 200
    assert recovery.client.get("/auth/me", headers={
        "Authorization": f"Bearer {new_login.json()['access_token']}",
    }).status_code == 200
    assert "password was changed" in recovery.outbox[-1]["Subject"]
    assert NEW_PASSWORD not in recovery.outbox[-1].get_content()


def test_unknown_email_has_the_same_response_and_does_not_create_an_account(recovery):
    email, _ = create_account(recovery)
    known = recovery.client.post("/auth/password-reset/request", json={"email": email})
    unknown_email = f"unknown-{uuid.uuid4().hex}@example.com"
    unknown = recovery.client.post("/auth/password-reset/request", json={"email": unknown_email})
    assert known.status_code == unknown.status_code == 202
    assert set(known.json()) == set(unknown.json())
    assert {k: v for k, v in known.json().items() if k != "challenge_id"} == {
        k: v for k, v in unknown.json().items() if k != "challenge_id"
    }
    assert len(recovery.outbox) == 1
    assert known.headers["cache-control"] == "no-store"
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == unknown_email)) is None
    assert confirm(recovery, unknown.json()["challenge_id"], "999999").status_code == 400


def test_code_is_hashed_and_not_exposed_in_the_response(recovery):
    email, _ = create_account(recovery)
    challenge_id, code = request_code(recovery, email)
    with SessionLocal() as db:
        row = db.scalar(select(PasswordResetChallenge).where(
            PasswordResetChallenge.id == uuid.UUID(challenge_id),
        ))
        assert row.code_digest != code
        assert len(row.code_digest) == 64
        assert row.email_key != email
    assert code not in recovery.client.get("/openapi.json").text


def test_wrong_code_cannot_change_password_or_authenticate(recovery):
    email, user_id = create_account(recovery)
    challenge_id, _ = request_code(recovery, email)
    result = confirm(recovery, challenge_id, "999999")
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == "invalid_code"
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert verify_password(PASSWORD, user.password_hash)
        assert user.auth_version == 0
    assert recovery.client.get("/history").status_code == 401


def test_code_expires_after_ten_minutes(recovery):
    email, _ = create_account(recovery)
    challenge_id, code = request_code(recovery, email)
    recovery.clock[0] += timedelta(seconds=600)
    result = confirm(recovery, challenge_id, code)
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == "expired_code"


def test_five_wrong_codes_exhaust_the_challenge_without_locking_login(recovery):
    email, _ = create_account(recovery)
    challenge_id, code = request_code(recovery, email)
    for _ in range(5):
        assert confirm(recovery, challenge_id, "999999").status_code == 400
    result = confirm(recovery, challenge_id, code)
    assert result.json()["detail"]["code"] == "attempts_exhausted"
    assert recovery.client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def test_resend_cooldown_is_case_insensitive_and_invalidates_the_old_code(recovery):
    email, _ = create_account(recovery)
    old_id, old_code = request_code(recovery, email)
    blocked = recovery.client.post("/auth/password-reset/request", json={"email": email.upper()})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"
    recovery.clock[0] += timedelta(seconds=60)
    new_id, new_code = request_code(recovery, email)
    assert new_id != old_id
    assert confirm(recovery, old_id, old_code).status_code == 400
    assert confirm(recovery, new_id, old_code).status_code == 400
    assert confirm(recovery, new_id, new_code).status_code == 200


def test_code_is_bound_to_the_correct_account_challenge(recovery):
    first_email, _ = create_account(recovery)
    second_email, _ = create_account(recovery)
    first_id, first_code = request_code(recovery, first_email)
    second_id, second_code = request_code(recovery, second_email)
    assert first_code != second_code
    assert confirm(recovery, second_id, first_code).status_code == 400
    assert confirm(recovery, first_id, second_code).status_code == 400
    assert confirm(recovery, first_id, first_code).status_code == 200


def test_concurrent_confirmation_consumes_the_code_only_once(recovery):
    email, _ = create_account(recovery)
    challenge_id, code = request_code(recovery, email)
    # Different clients avoid serialisation by the per-IP rate-limit lock.
    clients = [TestClient(app, client=(f"reset-race-{uuid.uuid4().hex}", 50000)) for _ in range(2)]
    def finish(client):
        return client.post("/auth/password-reset/confirm", json={
            "challenge_id": challenge_id, "code": code, "new_password": NEW_PASSWORD,
        }).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, clients))
    assert sorted(results) == [200, 400]


def test_email_request_limit_survives_new_requests_until_the_window_ends(recovery, monkeypatch):
    monkeypatch.setattr(password_reset, "MAX_EMAIL_REQUESTS", 2)
    email, _ = create_account(recovery)
    request_code(recovery, email)
    recovery.clock[0] += timedelta(seconds=60)
    request_code(recovery, email)
    recovery.clock[0] += timedelta(seconds=60)
    blocked = recovery.client.post("/auth/password-reset/request", json={"email": email})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 60
    recovery.clock[0] += timedelta(seconds=3600)
    assert recovery.client.post("/auth/password-reset/request", json={"email": email}).status_code == 202


def test_ip_request_limit_cannot_be_bypassed_by_forwarded_headers(recovery, monkeypatch):
    monkeypatch.setattr(password_reset, "MAX_IP_REQUESTS", 2)
    for _ in range(2):
        assert recovery.client.post("/auth/password-reset/request", json={
            "email": f"unknown-{uuid.uuid4().hex}@example.com",
        }).status_code == 202
    blocked = recovery.client.post("/auth/password-reset/request", json={
        "email": f"unknown-{uuid.uuid4().hex}@example.com",
    }, headers={"X-Forwarded-For": "192.0.2.123"})
    assert blocked.status_code == 429


def test_ip_verification_limit_applies_to_unknown_challenges(recovery, monkeypatch):
    monkeypatch.setattr(password_reset, "MAX_IP_VERIFICATIONS", 2)
    for _ in range(2):
        assert confirm(recovery, str(uuid.uuid4()), "999999").status_code == 400
    assert confirm(recovery, str(uuid.uuid4()), "999999").status_code == 429


@pytest.mark.parametrize("changes", [
    {"code": "12345"}, {"code": "abcdef"}, {"code": 123456},
    {"new_password": "short"}, {"new_password": " " * 8},
    {"new_password": "x" * 129}, {"purpose": "login"},
])
def test_invalid_reset_inputs_are_rejected_without_echoing_credentials(recovery, changes):
    payload = {
        "challenge_id": str(uuid.uuid4()), "code": "654321", "new_password": NEW_PASSWORD,
        **changes,
    }
    response = recovery.client.post("/auth/password-reset/confirm", json=payload)
    assert response.status_code == 422
    assert NEW_PASSWORD not in response.text
    assert "654321" not in response.text
    assert all("input" not in item for item in response.json()["detail"])


def test_delivery_failure_is_generic_and_invalidates_the_unsent_code(recovery, monkeypatch, caplog):
    email, _ = create_account(recovery)
    monkeypatch.setattr(email_delivery, "_send_message", lambda message: (_ for _ in ()).throw(OSError("private SMTP details")))
    result = recovery.client.post("/auth/password-reset/request", json={"email": email})
    assert result.status_code == 202
    assert "private SMTP details" not in result.text
    assert "private SMTP details" not in caplog.text
    assert email not in caplog.text
    with SessionLocal() as db:
        row = db.scalar(select(PasswordResetChallenge).where(
            PasswordResetChallenge.id == uuid.UUID(result.json()["challenge_id"]),
        ))
        assert row.code_digest is None
        assert row.consumed_at is not None


def test_unconfigured_email_fails_consistently_for_every_address(recovery, monkeypatch):
    email, _ = create_account(recovery)
    def unavailable():
        raise email_delivery.EmailDeliveryUnavailable("private config")
    monkeypatch.setattr(email_delivery, "ensure_email_configured", unavailable)
    known = recovery.client.post("/auth/password-reset/request", json={"email": email})
    unknown = recovery.client.post("/auth/password-reset/request", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 503
    assert known.json() == unknown.json()
    assert "private config" not in known.text


def test_kiswahili_email_is_delivered_without_the_code_in_the_subject(recovery):
    email, _ = create_account(recovery)
    _, code = request_code(recovery, email, "sw")
    assert "nenosiri" in recovery.outbox[-1]["Subject"]
    assert "Msimbo" in recovery.outbox[-1].get_content()
    assert code not in recovery.outbox[-1]["Subject"]


def test_legacy_tokens_work_until_the_account_password_is_reset(recovery):
    email, user_id = create_account(recovery)
    payload = decode_access_token(create_access_token(str(user_id), "patient"))
    payload.pop("auth_version")
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    headers = {"Authorization": f"Bearer {token}"}
    assert recovery.client.get("/auth/me", headers=headers).status_code == 200
    challenge_id, code = request_code(recovery, email)
    assert confirm(recovery, challenge_id, code).status_code == 200
    assert recovery.client.get("/auth/me", headers=headers).status_code == 401
