import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def override_database_dependency():
    def fake_get_db():
        yield object()

    app.dependency_overrides[
        main_module.get_db
    ] = fake_get_db

    yield

    app.dependency_overrides.clear()


@pytest.fixture
def mock_prediction_save(monkeypatch):
    def fake_save_prediction_session(
        db,
        **kwargs,
    ):
        return SimpleNamespace(
            id=uuid.uuid4()
        )

    monkeypatch.setattr(
        main_module,
        "save_prediction_session",
        fake_save_prediction_session,
    )


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
    assert data["has_red_flag"] is False
    assert data["feature_count"] == 198
    assert len(data["predictions"]) == 3
    assert "prediction_id" in data

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