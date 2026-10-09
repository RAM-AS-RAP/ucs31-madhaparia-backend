import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.core.config import Settings
from app.core.errors import ServiceNotReadyError
from app.main import (
    MAX_CONFIRMED_CODES,
    MAX_SYMPTOM_TEXT_LENGTH,
    app,
)
from app.nlp.symptom_mapper import build_confirmation_output


client = TestClient(app)


class EmptyCatalogueDb:
    def scalar(self, statement):
        return None


@pytest.fixture
def authenticated_patient():
    app.dependency_overrides[
        main_module.get_current_user
    ] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        email="patient@example.com",
        role="patient",
    )

    yield

    app.dependency_overrides.clear()


def test_empty_symptom_catalogue_is_reported():
    with pytest.raises(ServiceNotReadyError) as error:
        build_confirmation_output(
            "I have fever",
            EmptyCatalogueDb(),
        )

    assert "seed_symptom_catalog" in str(error.value)


def test_nlp_map_returns_503_when_catalogue_is_empty():
    app.dependency_overrides[
        main_module.get_db
    ] = lambda: EmptyCatalogueDb()

    try:
        response = client.post(
            "/nlp/map",
            json={
                "text": "I have fever"
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503

    assert (
        "seed_symptom_catalog"
        in response.json()["detail"]
    )


def test_predict_returns_503_without_active_model_version(
    monkeypatch,
    authenticated_patient,
):
    def no_active_model_version(db):
        raise ServiceNotReadyError(
            "No active machine-learning "
            "model version is configured."
        )

    monkeypatch.setattr(
        main_module,
        "get_active_model_version",
        no_active_model_version,
    )

    response = client.post(
        "/predict",
        json={
            "age": 25,
            "sex": "M",
            "text": "I have fever and cough",
            "confirmed_codes": [
                "fever",
                "cough",
            ],
        },
    )

    assert response.status_code == 503

    assert (
        "No active machine-learning model version"
        in response.json()["detail"]
    )


def test_symptom_text_too_long_returns_422():
    response = client.post(
        "/nlp/map",
        json={
            "text": "a" * (
                MAX_SYMPTOM_TEXT_LENGTH + 1
            )
        },
    )

    assert response.status_code == 422


def test_too_many_confirmed_codes_returns_422():
    response = client.post(
        "/symptoms/confirm",
        json={
            "text": "I have fever",
            "confirmed_codes": ["fever"] * (
                MAX_CONFIRMED_CODES + 1
            ),
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "change-me",
        "replace_with_a_secure_random_secret",
        "too-short",
    ],
)
def test_weak_jwt_secret_is_rejected(secret):
    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            jwt_secret_key=secret,
        )

    assert "JWT_SECRET_KEY must be set" in str(
        error.value
    )
