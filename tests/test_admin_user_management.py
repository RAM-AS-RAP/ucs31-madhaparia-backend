import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.admin_user_routes as routes
from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import AdminAccountAction, Feedback, PredictionSession, User
from app.main import app
from app.services.password_reset import complete_password_reset, request_password_reset, PasswordResetError

client = TestClient(app)
PASSWORD = "SyntheticAccountPass123!"


@pytest.fixture(scope="module")
def password_hash(test_database):
    return hash_password(PASSWORD)


def make_user(password_hash, role="patient", **values):
    with SessionLocal() as db:
        user = User(email=f"managed-{uuid.uuid4().hex}@example.com", password_hash=password_hash,
                    role=role, full_name="Synthetic Patient", **values)
        db.add(user)
        db.commit()
        db.refresh(user)
        return routes.public_account(user)


def headers(account, role=None):
    token = create_access_token(account["id"], role or account["role"], account["revision"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin(password_hash):
    return make_user(password_hash, "admin")


@pytest.fixture
def patient(password_hash):
    return make_user(password_hash)


def change(admin, account, action, revision=None):
    return client.patch(f"/admin/users/{account['id']}/status", headers=headers(admin), json={
        "action": action, "expected_revision": account["revision"] if revision is None else revision,
    })


def remove(admin, account, **values):
    return client.request("DELETE", f"/admin/users/{account['id']}", headers=headers(admin), json={
        "confirmation_email": account["email"], "admin_password": PASSWORD,
        "expected_revision": account["revision"], **values,
    })


def login(account):
    return client.post("/auth/login", json={"email": account["email"], "password": PASSWORD})


@pytest.mark.parametrize("active,blocked,code", [
    (False, False, "account_deactivated"),
    (True, True, "account_blocked"),
    (False, True, "account_blocked"),
])
def test_restricted_login_message_requires_the_correct_password(password_hash, active, blocked, code):
    account = make_user(password_hash, is_active=active, is_blocked=blocked)
    correct = login(account)
    assert correct.status_code == 403
    assert correct.json()["detail"]["code"] == code
    assert "Contact an administrator." in correct.json()["detail"]["message"]
    assert "access_token" not in correct.json()
    assert correct.headers["cache-control"] == "no-store"
    incorrect = client.post("/auth/login", json={"email": account["email"], "password": "IncorrectSyntheticPassword!"})
    unknown = client.post("/auth/login", json={"email": f"unknown-{uuid.uuid4().hex}@example.com", "password": PASSWORD})
    assert incorrect.status_code == unknown.status_code == 401
    assert incorrect.json() == unknown.json() == {"detail": "Invalid email or password."}


@pytest.mark.parametrize("identity", ["guest", "patient", "forged_admin"])
def test_all_management_endpoints_require_a_real_current_administrator(identity, patient):
    auth = {} if identity == "guest" else headers(patient, "admin" if identity == "forged_admin" else None)
    expected = 401 if identity == "guest" else 403
    requests = [
        ("GET", "/admin/users", None),
        ("POST", "/admin/users", {"email": "new@example.com", "full_name": "New Patient", "password": PASSWORD}),
        ("PATCH", f"/admin/users/{patient['id']}/status", {"action": "block", "expected_revision": 0}),
        ("DELETE", f"/admin/users/{patient['id']}", {"confirmation_email": patient["email"], "admin_password": PASSWORD, "expected_revision": 0}),
    ]
    for method, path, body in requests:
        response = client.request(method, path, headers=auth, **({"json": body} if body else {}))
        assert response.status_code == expected, response.text


def test_list_search_filters_and_pagination_use_authoritative_accounts(admin, password_hash):
    tag = f"search-{uuid.uuid4().hex}"
    accounts = [make_user(password_hash), make_user(password_hash, is_blocked=True), make_user(password_hash, is_active=False)]
    with SessionLocal() as db:
        for index, account in enumerate(accounts):
            db.get(User, uuid.UUID(account["id"])).full_name = f"{tag} Person {index}"
        db.commit()
    response = client.get("/admin/users", headers=headers(admin), params={"q": tag.upper(), "page_size": 2})
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 3 and len(result["users"]) == 2
    assert response.headers["cache-control"] == "no-store"
    assert all("password_hash" not in row and "password" not in row for row in result["users"])
    second = client.get("/admin/users", headers=headers(admin), params={"q": tag, "page_size": 2, "page": 2}).json()
    assert len(second["users"]) == 1
    assert {row["id"] for row in result["users"]}.isdisjoint({row["id"] for row in second["users"]})
    for status in ["active", "inactive", "blocked"]:
        filtered = client.get("/admin/users", headers=headers(admin), params={"q": tag, "status": status}).json()
        assert filtered["total"] == 1
    assert client.get("/admin/users", headers=headers(admin), params={"q": tag, "role": "admin"}).json()["total"] == 0
    assert client.get("/admin/users", headers=headers(admin), params={"q": "%"}).json()["total"] == 0


def test_admin_adds_normalised_patient_without_disclosing_credentials(admin):
    email = f"added-{uuid.uuid4().hex}@example.com"
    result = client.post("/admin/users", headers=headers(admin), json={
        "email": email.upper(), "full_name": "  Zoë   Patient  ", "phone_number": "+254 712-345-678", "password": PASSWORD,
    })
    assert result.status_code == 201, result.text
    account = result.json()
    assert account["email"] == email and account["full_name"] == "Zoë Patient"
    assert account["phone_number"] == "+254712345678"
    assert account["role"] == "patient" and account["is_active"] and not account["is_blocked"]
    assert PASSWORD not in result.text and "password_hash" not in result.text
    assert login(account).status_code == 200
    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(account["id"]))
        assert verify_password(PASSWORD, user.password_hash)
        assert db.scalar(select(AdminAccountAction).where(AdminAccountAction.target_user_id == user.id)).action == "create"
    duplicate = client.post("/admin/users", headers=headers(admin), json={"email": email, "full_name": "Other Patient", "password": PASSWORD})
    assert duplicate.status_code == 409


@pytest.mark.parametrize("changes", [{"role": "admin"}, {"is_active": False}, {"full_name": "1"}, {"phone_number": "123"}, {"password": "S3cret!"}, {"email": "invalid"}])
def test_creation_rejects_unapproved_fields_and_redacts_validation_payloads(admin, changes):
    payload = {"email": f"bad-{uuid.uuid4().hex}@example.com", "full_name": "New Patient", "password": PASSWORD, **changes}
    response = client.post("/admin/users", headers=headers(admin), json=payload)
    assert response.status_code == 422
    assert all("input" not in item for item in response.json()["detail"])
    assert payload["password"] not in response.text


def test_blocking_and_unblocking_revokes_tokens_and_never_revives_them(admin, patient):
    old_headers = headers(patient)
    blocked = change(admin, patient, "block")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["is_blocked"] and blocked.json()["is_active"]
    assert login(patient).status_code == 403
    assert client.get("/auth/me", headers=old_headers).status_code == 401
    unblocked = change(admin, blocked.json(), "unblock")
    assert unblocked.status_code == 200
    assert client.get("/history", headers=old_headers).status_code == 401
    assert login(patient).status_code == 200


def test_activation_and_blocking_are_independent(admin, patient):
    inactive = change(admin, patient, "deactivate").json()
    blocked = change(admin, inactive, "block").json()
    unblocked = change(admin, blocked, "unblock").json()
    assert not unblocked["is_active"] and not unblocked["is_blocked"]
    assert login(patient).status_code == 403
    active = change(admin, unblocked, "activate").json()
    assert active["is_active"]
    assert login(patient).status_code == 200
    blocked = change(admin, active, "block").json()
    still_blocked = change(admin, blocked, "activate").json()
    assert still_blocked["is_blocked"] and login(patient).status_code == 403


def test_protected_admins_and_self_cannot_be_changed_or_deleted(admin, password_hash):
    other_admin = make_user(password_hash, "admin")
    for target in [admin, other_admin]:
        for action in ["block", "deactivate", "unblock", "activate"]:
            assert change(admin, target, action).status_code == 403
        assert remove(admin, target).status_code == 403


def test_stale_versions_prevent_overwriting_another_admins_action(admin, patient):
    blocked = change(admin, patient, "block")
    assert blocked.status_code == 200
    assert change(admin, patient, "unblock").status_code == 409
    assert remove(admin, patient).status_code == 409


def test_concurrent_status_updates_cannot_both_apply_the_same_revision(admin, patient):
    def update(action):
        separate = TestClient(app)
        try:
            return separate.patch(f"/admin/users/{patient['id']}/status", headers=headers(admin), json={"action": action, "expected_revision": 0}).status_code
        finally:
            separate.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["block", "deactivate"]))
    assert sorted(results) == [200, 409]


def test_blocked_accounts_cannot_request_or_consume_password_recovery(admin, patient):
    with SessionLocal() as db:
        issued = request_password_reset(db, patient["email"], "en", f"host-{uuid.uuid4()}")
    blocked = change(admin, patient, "block").json()
    with SessionLocal() as db:
        with pytest.raises(PasswordResetError):
            complete_password_reset(db, issued.challenge_id, issued.code, "NewSyntheticPass123!", f"verify-{uuid.uuid4()}")
    change(admin, blocked, "unblock")
    with SessionLocal() as db:
        with pytest.raises(PasswordResetError):
            complete_password_reset(db, issued.challenge_id, issued.code, "NewSyntheticPass123!", f"verify-{uuid.uuid4()}")


def test_new_recovery_for_disabled_account_has_no_delivery_recipient(admin, patient):
    change(admin, patient, "deactivate")
    with SessionLocal() as db:
        issued = request_password_reset(db, patient["email"], "en", f"host-{uuid.uuid4()}")
    assert issued.email is None


def test_delete_requires_correct_admin_password_and_matching_email(admin, patient):
    assert remove(admin, patient, admin_password="incorrect").status_code == 403
    assert remove(admin, patient, confirmation_email="other@example.com").status_code == 400
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(patient["id"])) is not None
    response = remove(admin, patient, expected_revision="0")
    assert response.status_code == 422 and PASSWORD not in response.text


def test_failed_delete_password_checks_are_rate_limited(password_hash, patient):
    isolated_admin = make_user(password_hash, "admin")
    for _ in range(5):
        assert remove(isolated_admin, patient, admin_password="incorrect").status_code == 403
    denied = remove(isolated_admin, patient)
    assert denied.status_code == 429 and denied.headers["retry-after"] == "900"


def test_delete_removes_only_target_account_and_its_assessments_and_feedback(admin, patient, password_hash):
    other = make_user(password_hash)
    ids = []
    with SessionLocal() as db:
        for account in [patient, other]:
            session = PredictionSession(user_id=uuid.UUID(account["id"]), age=25, sex="F", input_text="Synthetic", confirmed_codes=["cough"], model_features=[], status="prediction_complete", has_red_flag=False, predictions=[])
            db.add(session)
            db.flush()
            db.add(Feedback(prediction_session_id=session.id, was_helpful=True))
            ids.append(session.id)
        db.commit()
        issued = request_password_reset(db, patient["email"], "en", f"host-{uuid.uuid4()}")
    result = remove(admin, patient)
    assert result.status_code == 200, result.text
    assert result.json() == {"status": "deleted", "user_id": patient["id"]}
    assert client.get("/auth/me", headers=headers(patient)).status_code == 401
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(patient["id"])) is None
        assert db.get(PredictionSession, ids[0]) is None
        assert db.scalar(select(Feedback).where(Feedback.prediction_session_id == ids[0])) is None
        assert db.get(User, uuid.UUID(other["id"])) is not None
        assert db.get(PredictionSession, ids[1]) is not None
        assert db.scalar(select(AdminAccountAction).where(AdminAccountAction.target_user_id == uuid.UUID(patient["id"]), AdminAccountAction.action == "delete")) is not None
        with pytest.raises(PasswordResetError):
            complete_password_reset(db, issued.challenge_id, issued.code, "NewSyntheticPass123!", f"verify-{uuid.uuid4()}")


def test_deletion_rolls_back_if_the_audit_cannot_be_saved(admin, patient, monkeypatch):
    def fail(*args):
        raise RuntimeError("Synthetic audit failure")
    monkeypatch.setattr(routes, "audit", fail)
    with TestClient(app, raise_server_exceptions=False) as errors:
        response = errors.request("DELETE", f"/admin/users/{patient['id']}", headers=headers(admin), json={"confirmation_email": patient["email"], "admin_password": PASSWORD, "expected_revision": 0})
    assert response.status_code == 500
    with SessionLocal() as db:
        assert db.get(User, uuid.UUID(patient["id"])) is not None
