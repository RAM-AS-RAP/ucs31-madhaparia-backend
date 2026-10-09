import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import SessionLocal
from app.main import app


client = TestClient(app)

TEST_USER_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def override_dependencies():
    def fake_get_db():
        db = SessionLocal()

        try:
            yield db
        finally:
            db.close()

    def fake_get_current_user():
        return SimpleNamespace(
            id=TEST_USER_ID,
            email="patient@example.com",
            role="patient",
        )

    app.dependency_overrides[
        main_module.get_db
    ] = fake_get_db

    app.dependency_overrides[
        main_module.get_current_user
    ] = fake_get_current_user

    yield

    app.dependency_overrides.clear()


@pytest.fixture
def mock_prediction_save(monkeypatch):
    saved = {}

    def fake_save_prediction_session(
        db,
        **kwargs,
    ):
        saved.update(kwargs)

        return SimpleNamespace(
            id=uuid.uuid4()
        )

    monkeypatch.setattr(
        main_module,
        "save_prediction_session",
        fake_save_prediction_session,
    )

    return saved


def test_normal_prediction(
    mock_prediction_save,
):
    response = client.post(
        "/predict",
        json={
            "age": 25,
            "sex": "M",
            "text": "I have fever na kikohozi",
            "confirmed_codes": [
                "fever",
                "cough",
            ],
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "prediction_complete"
    assert data["model_version"] == "1.0"
    assert data["has_red_flag"] is False
    assert data["feature_count"] == 198
    assert len(data["predictions"]) == 3
    assert "prediction_id" in data

    assert (
        mock_prediction_save["user_id"]
        == TEST_USER_ID
    )

    assert (
        mock_prediction_save["model_version_id"]
        is not None
    )

    for prediction in data["predictions"]:
        assert "rank" in prediction
        assert "condition" in prediction
        assert "display_name" in prediction
        assert "model_confidence_percent" in prediction
        assert "description" in prediction

    assert "confidence_explanation" in data
    assert "health_guidance" in data
    assert "disclaimer" in data


def test_red_flag_blocks_prediction(
    mock_prediction_save,
):
    response = client.post(
        "/predict",
        json={
            "age": 25,
            "sex": "M",
            "text": "I am coughing up blood",
            "confirmed_codes": [
                "coughing_blood",
            ],
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "urgent_review"
    assert data["has_red_flag"] is True
    assert data["predictions"] == []
    assert "prediction_id" in data
    assert "health_guidance" in data
    assert "disclaimer" in data

    assert (
        mock_prediction_save["user_id"]
        == TEST_USER_ID
    )

    assert (
        mock_prediction_save["model_version_id"]
        is None
    )


def test_invalid_age():
    response = client.post(
        "/predict",
        json={
            "age": 150,
            "sex": "M",
            "text": "I have fever",
            "confirmed_codes": [
                "fever",
            ],
        },
    )

    assert response.status_code == 422


def test_invalid_sex():
    response = client.post(
        "/predict",
        json={
            "age": 25,
            "sex": "X",
            "text": "I have fever",
            "confirmed_codes": [
                "fever",
            ],
        },
    )

    assert response.status_code == 422


def test_invalid_confirmed_symptom():
    response = client.post(
        "/predict",
        json={
            "age": 25,
            "sex": "M",
            "text": "I have fever",
            "confirmed_codes": [
                "skin_rash",
            ],
        },
    )

    assert response.status_code == 400

    assert (
        "skin_rash"
        in response.json()["detail"]
    )


def test_prediction_requires_confirmed_model_feature():
    response = client.post(
        "/predict",
        json={
            "age": 25,
            "sex": "M",
            "text": "I have fever",
            "confirmed_codes": [],
        },
    )

    assert response.status_code == 400

    assert (
        "At least one confirmed model symptom feature"
        in response.json()["detail"]
    )

def test_prediction_requires_authentication():
    app.dependency_overrides.pop(
        main_module.get_current_user,
        None,
    )

    response = client.post(
        "/predict",
        json={
            "age": 25,
            "sex": "M",
            "text": "I have fever",
            "confirmed_codes": [
                "fever",
            ],
        },
    )

    assert response.status_code == 401

    assert (
        response.json()["detail"]
        == "Authentication required."
    )