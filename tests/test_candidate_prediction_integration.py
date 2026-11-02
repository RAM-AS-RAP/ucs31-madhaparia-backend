"""Real candidate/API/history checks inside pytest's temporary schema."""

import re
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

import app.main as main
from app.core.database import SessionLocal
from app.core.errors import ServiceNotReadyError
from app.core.security import create_access_token
from app.db.models import ModelVersion, PredictionSession, User
from app.db.register_candidate_model_version import (
    register_candidate_model_version,
)
from app.services import candidate_prediction as candidate_service


@pytest.fixture(scope="module")
def environment(test_database):
    assert not main.app.dependency_overrides

    with SessionLocal() as db:
        assert db.scalar(text("SELECT current_database()")) == (
            "respiratory_test"
        )
        schema = db.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{12}", schema), schema

        previous_states = {
            row.id: row.is_active
            for row in db.scalars(select(ModelVersion)).all()
        }

        candidate = register_candidate_model_version(db)
        candidate_id = candidate.id
        candidate_was_created = candidate_id not in previous_states

        for row in db.scalars(select(ModelVersion)).all():
            row.is_active = row.id == candidate_id

        users = {}
        for role_name, database_role in (
            ("patient", "patient"),
            ("other", "patient"),
            ("admin", "admin"),
        ):
            user = User(
                email=f"candidate-{role_name}-{uuid.uuid4().hex}@example.com",
                password_hash="test-only-not-a-real-password",
                role=database_role,
            )
            db.add(user)
            db.flush()
            users[role_name] = user.id

        db.commit()

    headers = {
        name: {
            "Authorization": "Bearer " + create_access_token(
                user_id=str(user_id),
                role="admin" if name == "admin" else "patient",
            )
        }
        for name, user_id in users.items()
    }

    try:
        with TestClient(main.app) as client:
            yield SimpleNamespace(
                client=client,
                headers=headers,
                users=users,
                candidate_id=candidate_id,
            )
    finally:
        with SessionLocal() as db:
            for row in db.scalars(select(ModelVersion)).all():
                row.is_active = previous_states.get(row.id, False)

            if candidate_was_created:
                candidate = db.get(ModelVersion, candidate_id)
                if candidate is not None:
                    db.delete(candidate)

            db.commit()


def submit(environment, symptom_text, codes):
    response = environment.client.post(
        "/predict",
        headers=environment.headers["patient"],
        json={
            "age": 22,
            "sex": "F",
            "text": symptom_text,
            "confirmed_codes": codes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def patient_row_count(environment):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(PredictionSession)
            .where(
                PredictionSession.user_id == environment.users["patient"]
            )
        )


@pytest.mark.parametrize(
    ("symptom_text", "codes", "status", "units", "performed"),
    [
        (
            "I have tiredness and headache",
            ["tiredness", "headache"],
            "insufficient_information",
            2,
            True,
        ),
        (
            "I have fever and cough",
            ["fever", "cough"],
            "insufficient_information",
            2,
            True,
        ),
        (
            "I have tiredness and chest pain",
            ["tiredness", "chest_pain"],
            "prediction_complete",
            2,
            True,
        ),
        (
            "I have tiredness",
            ["tiredness"],
            "insufficient_information",
            1,
            False,
        ),
        (
            "I have fever and cough",
            [],
            "insufficient_information",
            0,
            False,
        ),
        (
            "I have itchy eyes",
            ["itchy_eyes"],
            "unsupported_input",
            0,
            False,
        ),
        (
            "I have fever and itchy eyes",
            ["fever", "itchy_eyes"],
            "unsupported_input",
            1,
            False,
        ),
    ],
)
def test_real_candidate_assessment_is_saved(
    environment, symptom_text, codes, status, units, performed
):
    result = submit(environment, symptom_text, codes)
    assessment = result["assessment"]

    assert result["status"] == status
    assert result["feature_count"] == 70
    assert result["model_version"] == "candidate_v1_research"
    assert assessment["reported_unit_count"] == units
    assert assessment["prediction_performed"] is performed
    assert assessment["mapping_version"] == (
        "backend_candidate_symptom_mapping_v1"
    )
    assert assessment["probability_representation"] == "temperature_scaled"
    assert assessment["bundle_manifest_sha256"] == (
        "52ee494aedd887210a0c4ca991d94321321af0e3766ee7b63f0742867730858b"
    )
    assert "diagnostics" not in assessment
    assert "ranked_conditions" not in assessment

    if status == "prediction_complete":
        # Regression expectation from the frozen, unlabelled probe.
        # This does not establish that the condition is clinically correct.
        assert assessment["predicted_condition"] == "Pulmonary neoplasm"
        assert assessment["model_score"] == pytest.approx(
            0.7801629519255993, abs=1e-12, rel=0
        )
        assert len(result["predictions"]) == 1
        assert result["predictions"][0]["condition"] == (
            assessment["predicted_condition"]
        )
        assert result["predictions"][0]["model_confidence_percent"] == round(
            assessment["model_score"] * 100, 2
        )
    else:
        assert result["predictions"] == []
        assert assessment["predicted_condition"] is None
        assert assessment["model_score"] is None
        assert assessment["uncertainty_reasons"]

    if status == "unsupported_input":
        assert assessment["unsupported_symptoms"] == ["itchy_eyes"]
        assert assessment["clarification_required"][0]["code"] == "itchy_eyes"

    prediction_id = uuid.UUID(result["prediction_id"])
    with SessionLocal() as db:
        saved = db.get(PredictionSession, prediction_id)

        assert saved.user_id == environment.users["patient"]
        assert saved.model_version_id == environment.candidate_id
        assert saved.status == status
        assert saved.assessment == assessment
        assert saved.predictions == result["predictions"]
        assert saved.confirmed_codes == result["confirmed_codes"]
        assert saved.model_features == result["model_features"]

    history = environment.client.get(
        f"/history/{prediction_id}",
        headers=environment.headers["patient"],
    )
    assert history.status_code == 200, history.text
    assert history.json()["assessment"] == assessment
    assert history.json()["predictions"] == result["predictions"]


def test_real_history_ownership_and_admin_access(environment):
    result = submit(
        environment,
        "I have tiredness and headache",
        ["tiredness", "headache"],
    )
    prediction_id = result["prediction_id"]

    other_patient = environment.client.get(
        f"/history/{prediction_id}",
        headers=environment.headers["other"],
    )
    assert other_patient.status_code == 404

    guest = environment.client.get(f"/history/{prediction_id}")
    assert guest.status_code == 401

    forbidden_admin = environment.client.get(
        f"/admin/history/{prediction_id}",
        headers=environment.headers["patient"],
    )
    assert forbidden_admin.status_code == 403

    admin_detail = environment.client.get(
        f"/admin/history/{prediction_id}",
        headers=environment.headers["admin"],
    )
    assert admin_detail.status_code == 200, admin_detail.text
    assert admin_detail.json()["assessment"] == result["assessment"]
    assert admin_detail.json()["predictions"] == result["predictions"]

    for path, identity in (
        ("/history", "patient"),
        ("/admin/history", "admin"),
    ):
        response = environment.client.get(
            path,
            headers=environment.headers[identity],
        )
        assert response.status_code == 200, response.text

        matches = [
            item
            for item in response.json()["history"]
            if item["prediction_id"] == prediction_id
        ]
        assert len(matches) == 1
        assert matches[0]["assessment"] == result["assessment"]


def test_unavailable_runtime_does_not_block_independent_safety(
    environment, monkeypatch
):
    unavailable = Mock(
        side_effect=ServiceNotReadyError("Internal artifact failure.")
    )
    monkeypatch.setattr(
        candidate_service, "load_candidate_runtime", unavailable
    )

    before = patient_row_count(environment)

    failed = environment.client.post(
        "/predict",
        headers=environment.headers["patient"],
        json={
            "age": 22,
            "sex": "F",
            "text": "I have fever and cough",
            "confirmed_codes": ["fever", "cough"],
        },
    )
    assert failed.status_code == 503
    assert patient_row_count(environment) == before

    unavailable.reset_mock()

    urgent = submit(
        environment,
        "My lips are turning blue",
        ["blue_lips"],
    )
    unavailable.assert_not_called()

    assert urgent["status"] == "urgent_review"
    assert urgent["predictions"] == []
    assert urgent["assessment"]["prediction_performed"] is False
    assert urgent["assessment"]["model_version"] is None
    assert urgent["assessment"]["policy_id"] is None
    assert urgent["assessment"]["safety_flags"] == ["blue_lips"]

    with SessionLocal() as db:
        saved = db.get(
            PredictionSession,
            uuid.UUID(urgent["prediction_id"]),
        )
        assert saved.model_version_id is None
        assert saved.assessment == urgent["assessment"]
        assert saved.predictions == []


def test_registration_mismatch_prevents_saving(environment):
    with SessionLocal() as db:
        candidate = db.get(ModelVersion, environment.candidate_id)
        original_feature_count = candidate.feature_count
        candidate.feature_count = 66
        db.commit()

    try:
        before = patient_row_count(environment)

        response = environment.client.post(
            "/predict",
            headers=environment.headers["patient"],
            json={
                "age": 22,
                "sex": "F",
                "text": "I have fever and cough",
                "confirmed_codes": ["fever", "cough"],
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"] == (
            "The candidate assessment service is unavailable."
        )
        assert patient_row_count(environment) == before
    finally:
        with SessionLocal() as db:
            candidate = db.get(ModelVersion, environment.candidate_id)
            candidate.feature_count = original_feature_count
            db.commit()