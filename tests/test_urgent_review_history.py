import uuid
from unittest.mock import Mock

from fastapi.testclient import TestClient

import app.main as main
from app.core.database import SessionLocal
from app.db.models import PredictionSession, User


client = TestClient(main.app)
PASSWORD = "SyntheticTestPassword123!"

URGENT_INPUT = {
    "text": "I have blue lips",
    "confirmed_codes": ["blue_lips"],
}


def create_account(role="patient"):
    email = f"safety-{uuid.uuid4().hex}@example.com"

    response = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text

    user_id = uuid.UUID(response.json()["id"])

    if role == "admin":
        with SessionLocal() as db:
            user = db.get(User, user_id)
            user.role = "admin"
            db.commit()

    response = client.post(
        "/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text

    return user_id, {
        "Authorization": f"Bearer {response.json()['access_token']}"
    }


def test_urgent_review_is_saved_without_demographics_or_model_work(
    monkeypatch,
):
    guards = []

    for name in (
        "get_active_model_version",
        "validate_candidate_registration",
        "assess_candidate_confirmation",
        "predict_v2_single",
        "predict_top_conditions",
    ):
        guard = Mock(
            side_effect=AssertionError(
                "A safety review must not run model work."
            )
        )
        monkeypatch.setattr(main, name, guard)
        guards.append(guard)

    owner_id, owner_headers = create_account()
    _, other_headers = create_account()
    _, admin_headers = create_account(role="admin")

    response = client.post(
        "/safety/reviews",
        headers=owner_headers,
        json=URGENT_INPUT,
    )
    assert response.status_code == 200, response.text

    result = response.json()
    prediction_id = result["prediction_id"]

    assert result["status"] == "urgent_review"
    assert result["age"] is None
    assert result["sex"] is None
    assert result["predictions"] == []
    assert result["has_red_flag"] is True
    assert result["health_guidance"]
    assert result["assessment"]["prediction_performed"] is False

    with SessionLocal() as db:
        saved = db.get(
            PredictionSession,
            uuid.UUID(prediction_id),
        )
        assert saved is not None
        assert saved.user_id == owner_id
        assert saved.age is None
        assert saved.sex is None
        assert saved.status == "urgent_review"
        assert saved.model_version_id is None
        assert saved.predictions == []
        assert saved.assessment == result["assessment"]

    response = client.get("/history", headers=owner_headers)
    assert response.status_code == 200
    assert any(
        item["prediction_id"] == prediction_id
        for item in response.json()["history"]
    )

    response = client.get(
        f"/history/{prediction_id}",
        headers=owner_headers,
    )
    assert response.status_code == 200
    assert response.json()["assessment"] == result["assessment"]

    response = client.get(
        f"/history/{prediction_id}",
        headers=other_headers,
    )
    assert response.status_code == 404

    response = client.get(
        f"/admin/history/{prediction_id}",
        headers=owner_headers,
    )
    assert response.status_code == 403

    response = client.get(
        "/admin/history",
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert any(
        item["prediction_id"] == prediction_id
        and item["status"] == "urgent_review"
        for item in response.json()["history"]
    )

    response = client.get(
        f"/admin/history/{prediction_id}",
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["assessment"] == result["assessment"]

    for guard in guards:
        guard.assert_not_called()


def test_safety_review_requires_authentication():
    response = client.post(
        "/safety/reviews",
        json=URGENT_INPUT,
    )
    assert response.status_code == 401


def test_safety_review_rejects_nonurgent_symptoms():
    _, headers = create_account()

    response = client.post(
        "/safety/reviews",
        headers=headers,
        json={
            "text": "I have a cough",
            "confirmed_codes": ["cough"],
        },
    )

    assert response.status_code == 400
    assert "red-flag" in response.json()["detail"]


def test_normal_prediction_still_requires_age_and_sex():
    _, headers = create_account()

    response = client.post(
        "/predict",
        headers=headers,
        json={
            "text": "I have a cough",
            "confirmed_codes": ["cough"],
        },
    )

    assert response.status_code == 422