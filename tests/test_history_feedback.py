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


def test_history_endpoint_not_public():
    response = client.get("/history")

    assert response.status_code == 404


def test_history_item_endpoint_not_public():
    response = client.get(
        f"/history/{uuid.uuid4()}"
    )

    assert response.status_code == 404


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
            "comment": "The information was useful.",
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