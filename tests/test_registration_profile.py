import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security import verify_password
from app.db.models import User
from app.main import app


client = TestClient(app)
PASSWORD = "SyntheticPassword123!"


def registration_payload(**changes):
    return {
        "email": f"profile-{uuid.uuid4().hex}@example.com",
        "password": PASSWORD,
        "full_name": "Example Patient",
        "phone_number": "+254712345678",
        **changes,
    }


def create_profile_account():
    payload = registration_payload()

    registration = client.post(
        "/auth/register",
        json=payload,
    )
    assert registration.status_code == 201, registration.text

    login = client.post(
        "/auth/login",
        json={
            "email": payload["email"],
            "password": PASSWORD,
        },
    )
    assert login.status_code == 200, login.text

    headers = {
        "Authorization": (
            f"Bearer {login.json()['access_token']}"
        ),
    }

    return registration.json(), headers


def test_profile_is_normalised_saved_and_returned_by_me():
    payload = registration_payload(
        full_name="  Zoë   Njeri  ",
        phone_number="+254 712-345-678",
    )

    response = client.post(
        "/auth/register",
        json=payload,
    )
    assert response.status_code == 201, response.text

    result = response.json()

    assert result["full_name"] == "Zoë Njeri"
    assert result["phone_number"] == "+254712345678"
    assert result["role"] == "patient"
    assert "password" not in result
    assert "password_hash" not in result

    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(result["id"]))

        assert user.full_name == "Zoë Njeri"
        assert user.phone_number == "+254712345678"
        assert user.password_hash != PASSWORD
        assert verify_password(PASSWORD, user.password_hash)

    login = client.post(
        "/auth/login",
        json={
            "email": payload["email"],
            "password": PASSWORD,
        },
    )
    assert login.status_code == 200, login.text

    me = client.get(
        "/auth/me",
        headers={
            "Authorization": (
                f"Bearer {login.json()['access_token']}"
            ),
        },
    )

    assert me.status_code == 200, me.text
    assert me.json()["full_name"] == "Zoë Njeri"
    assert me.json()["phone_number"] == "+254712345678"
    assert "password_hash" not in me.json()


@pytest.mark.parametrize("phone_number", [None, "   "])
def test_phone_number_is_optional(phone_number):
    response = client.post(
        "/auth/register",
        json=registration_payload(phone_number=phone_number),
    )

    assert response.status_code == 201, response.text
    assert response.json()["phone_number"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"full_name": ""},
        {"full_name": "A"},
        {"full_name": "12345"},
        {"full_name": "A" * 101},
        {"full_name": "Example\nPatient"},
        {"full_name": 123},
        {"phone_number": "0712345678"},
        {"phone_number": "+254CALLME"},
        {"phone_number": "+123"},
        {"phone_number": "+" + "1" * 16},
        {"phone_number": 123},
        {"password": " " * 8},
    ],
)
def test_invalid_profile_or_password_is_rejected(changes):
    response = client.post(
        "/auth/register",
        json=registration_payload(**changes),
    )

    assert response.status_code == 422, response.text
    assert "access_token" not in response.json()


def test_older_registration_clients_remain_supported():
    response = client.post(
        "/auth/register",
        json={
            "email": f"legacy-{uuid.uuid4().hex}@example.com",
            "password": PASSWORD,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["full_name"] is None
    assert response.json()["phone_number"] is None


def test_duplicate_email_cannot_overwrite_existing_profile():
    payload = registration_payload()

    first = client.post(
        "/auth/register",
        json=payload,
    )
    assert first.status_code == 201, first.text

    duplicate = client.post(
        "/auth/register",
        json={
            **payload,
            "email": payload["email"].upper(),
            "full_name": "Different Person",
            "phone_number": "+254722345678",
        },
    )
    assert duplicate.status_code == 409, duplicate.text

    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(first.json()["id"]))

        assert user.full_name == "Example Patient"
        assert user.phone_number == "+254712345678"


def test_profile_registration_cannot_create_an_administrator():
    response = client.post(
        "/auth/register",
        json=registration_payload(role="admin"),
    )

    assert response.status_code == 201, response.text
    assert response.json()["role"] == "patient"


def test_profile_update_requires_authentication():
    response = client.patch(
        "/auth/me",
        json={"full_name": "Updated Patient"},
    )

    assert response.status_code == 401


def test_profile_update_changes_only_the_authenticated_account():
    owner, headers = create_profile_account()
    other, _ = create_profile_account()

    response = client.patch(
        "/auth/me",
        headers=headers,
        json={
            "full_name": "  Updated   Patient  ",
            "phone_number": "+254 722-345-678",
        },
    )
    assert response.status_code == 200, response.text

    result = response.json()

    assert result["id"] == owner["id"]
    assert result["email"] == owner["email"]
    assert result["role"] == "patient"
    assert result["full_name"] == "Updated Patient"
    assert result["phone_number"] == "+254722345678"
    assert "password" not in result
    assert "password_hash" not in result

    with SessionLocal() as db:
        saved_owner = db.get(User, uuid.UUID(owner["id"]))
        saved_other = db.get(User, uuid.UUID(other["id"]))

        assert saved_owner.full_name == "Updated Patient"
        assert saved_owner.phone_number == "+254722345678"

        assert saved_other.full_name == other["full_name"]
        assert saved_other.phone_number == other["phone_number"]

    me = client.get("/auth/me", headers=headers)

    assert me.status_code == 200
    assert me.json()["full_name"] == "Updated Patient"
    assert me.json()["phone_number"] == "+254722345678"


def test_partial_profile_update_preserves_other_fields():
    owner, headers = create_profile_account()

    response = client.patch(
        "/auth/me",
        headers=headers,
        json={"full_name": "New Patient Name"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "New Patient Name"
    assert response.json()["phone_number"] == owner["phone_number"]
    assert response.json()["email"] == owner["email"]


def test_optional_phone_number_can_be_cleared():
    owner, headers = create_profile_account()

    response = client.patch(
        "/auth/me",
        headers=headers,
        json={"phone_number": None},
    )

    assert response.status_code == 200, response.text
    assert response.json()["phone_number"] is None
    assert response.json()["full_name"] == owner["full_name"]


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "admin"},
        {"email": "different@example.com"},
        {"password": "DifferentPassword123!"},
        {"user_id": str(uuid.uuid4())},
        {"id": str(uuid.uuid4())},
        {"full_name": None},
        {"full_name": ""},
        {"phone_number": "invalid"},
    ],
)
def test_invalid_or_protected_profile_changes_are_rejected(changes):
    owner, headers = create_profile_account()

    response = client.patch(
        "/auth/me",
        headers=headers,
        json=changes,
    )
    assert response.status_code == 422, response.text

    me = client.get("/auth/me", headers=headers)

    assert me.status_code == 200
    assert me.json()["email"] == owner["email"]
    assert me.json()["role"] == owner["role"]
    assert me.json()["full_name"] == owner["full_name"]
    assert me.json()["phone_number"] == owner["phone_number"]


def test_empty_profile_update_is_rejected():
    _, headers = create_profile_account()

    response = client.patch(
        "/auth/me",
        headers=headers,
        json={},
    )

    assert response.status_code == 400, response.text