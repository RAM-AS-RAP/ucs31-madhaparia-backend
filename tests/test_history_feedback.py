import uuid
from datetime import datetime, timezone
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


def make_prediction_session():
    return SimpleNamespace(
        id=uuid.uuid4(),
        age=25,
        sex="M",
        input_text="I have fever and cough",
        confirmed_codes=["fever", "cough"],
        model_features=["E_91", "E_201"],
        status="prediction_complete",
        has_red_flag=False,
        predictions=[
            {
                "rank": 1,
                "condition": "Tuberculosis",
                "display_name": "Tuberculosis (TB)",
                "model_confidence_percent": 99.75,
                "description": "Test description.",
            }
        ],
        created_at=datetime.now(timezone.utc),
    )


def test_history_list(monkeypatch):
    session = make_prediction_session()

    monkeypatch.setattr(
        main_module,
        "list_prediction_sessions",
        lambda db, limit=20: [session],
    )

    response = client.get("/history")

    assert response.status_code == 200

    data = response.json()

    assert data["count"] == 1
    assert len(data["history"]) == 1

    item = data["history"][0]

    assert item["prediction_id"] == str(session.id)
    assert item["age"] == 25
    assert item["sex"] == "M"
    assert item["status"] == "prediction_complete"


def test_history_item(monkeypatch):
    session = make_prediction_session()

    monkeypatch.setattr(
        main_module,
        "get_prediction_session",
        lambda db, prediction_id: session,
    )

    response = client.get(
        f"/history/{session.id}"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["prediction_id"] == str(session.id)
    assert data["age"] == 25
    assert data["confirmed_codes"] == [
        "fever",
        "cough",
    ]


def test_history_item_not_found(monkeypatch):
    prediction_id = uuid.uuid4()

    monkeypatch.setattr(
        main_module,
        "get_prediction_session",
        lambda db, prediction_id: None,
    )

    response = client.get(
        f"/history/{prediction_id}"
    )

    assert response.status_code == 404

    assert (
        response.json()["detail"]
        == "Prediction session not found."
    )


def test_invalid_history_uuid():
    response = client.get(
        "/history/not-a-valid-uuid"
    )

    assert response.status_code == 422


def test_feedback_success(monkeypatch):
    prediction_id = uuid.uuid4()
    feedback_id = uuid.uuid4()

    fake_feedback = SimpleNamespace(
        id=feedback_id,
        prediction_session_id=prediction_id,
        was_helpful=True,
        comment="The information was useful.",
        created_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(
        main_module,
        "save_feedback",
        lambda db,
        prediction_session_id,
        was_helpful,
        comment: fake_feedback,
    )

    response = client.post(
        "/feedback",
        json={
            "prediction_id": str(prediction_id),
            "was_helpful": True,
            "comment": (
                "The information was useful."
            ),
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "feedback_saved"
    assert data["feedback_id"] == str(feedback_id)
    assert data["prediction_id"] == str(
        prediction_id
    )
    assert data["was_helpful"] is True


def test_duplicate_feedback(monkeypatch):
    prediction_id = uuid.uuid4()

    def fake_save_feedback(*args, **kwargs):
        raise ValueError(
            "Feedback has already been submitted "
            "for this prediction session."
        )

    monkeypatch.setattr(
        main_module,
        "save_feedback",
        fake_save_feedback,
    )

    response = client.post(
        "/feedback",
        json={
            "prediction_id": str(prediction_id),
            "was_helpful": True,
            "comment": "Test feedback.",
        },
    )

    assert response.status_code == 400

    assert (
        "Feedback has already been submitted"
        in response.json()["detail"]
    )


def test_feedback_comment_too_long():
    response = client.post(
        "/feedback",
        json={
            "prediction_id": str(uuid.uuid4()),
            "was_helpful": True,
            "comment": "A" * 1001,
        },
    )

    assert response.status_code == 422