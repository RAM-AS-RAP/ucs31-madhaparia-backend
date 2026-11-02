"""Activation and rollback rehearsal restricted to pytest's test schema."""

import re
import uuid
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

import app.main as main
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.db.activate_candidate_model_version import (
    CandidateActivationError,
    activate_candidate_model_version,
)
from app.db.activate_v2_model_version import activate_v2_model_version
from app.db.models import ModelVersion, PredictionSession, User
from app.db.register_candidate_model_version import (
    register_candidate_model_version,
)
from app.db.register_v2_model_version import register_v2_model_version
from app.ml.model_loader import V2_MODEL_VERSION


def active_versions(db):
    return list(
        db.scalars(
            select(ModelVersion.version)
            .where(ModelVersion.is_active.is_(True))
            .order_by(ModelVersion.version)
        ).all()
    )


def history_snapshot(prediction_ids):
    with SessionLocal() as db:
        result = {}
        for prediction_id in prediction_ids:
            row = db.get(PredictionSession, prediction_id)
            assert row is not None
            result[str(prediction_id)] = {
                "status": row.status,
                "model_version_id": str(row.model_version_id),
                "predictions": deepcopy(row.predictions),
                "assessment": deepcopy(row.assessment),
                "confirmed_codes": deepcopy(row.confirmed_codes),
                "model_features": deepcopy(row.model_features),
            }
        return result


def test_candidate_activation_and_v2_rollback_preserve_history(test_database):
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

        register_v2_model_version(db)
        candidate = register_candidate_model_version(db)
        candidate_id = candidate.id

        activate_v2_model_version(db)

        patient = User(
            email=f"rollback-rehearsal-{uuid.uuid4().hex}@example.com",
            password_hash="test-only-not-a-real-password",
            role="patient",
        )
        db.add(patient)
        db.flush()
        patient_id = patient.id
        db.commit()

    headers = {
        "Authorization": "Bearer " + create_access_token(
            user_id=str(patient_id),
            role="patient",
        )
    }

    try:
        # A stale expectation must not switch models.
        with SessionLocal() as db:
            with pytest.raises(CandidateActivationError, match="changed"):
                activate_candidate_model_version(
                    db,
                    expected_previous_version="unexpected-version",
                )
            assert active_versions(db) == [V2_MODEL_VERSION]
            db.rollback()

        # Conflicting registration metadata must also prevent switching.
        with SessionLocal() as db:
            candidate = db.get(ModelVersion, candidate_id)
            candidate.feature_count = 66
            db.flush()

            with pytest.raises(CandidateActivationError, match="metadata"):
                activate_candidate_model_version(
                    db,
                    expected_previous_version=V2_MODEL_VERSION,
                )

            assert active_versions(db) == [V2_MODEL_VERSION]
            db.rollback()

        # Activation is rollbackable until the caller commits.
        with SessionLocal() as db:
            activate_candidate_model_version(
                db,
                expected_previous_version=V2_MODEL_VERSION,
            )
            assert active_versions(db) == ["candidate_v1_research"]
            db.rollback()

        with SessionLocal() as db:
            assert active_versions(db) == [V2_MODEL_VERSION]

        # Commit candidate activation only inside this temporary schema.
        with SessionLocal.begin() as db:
            activated = activate_candidate_model_version(
                db,
                expected_previous_version=V2_MODEL_VERSION,
            )
            assert activated.id == candidate_id

        with SessionLocal() as db:
            assert active_versions(db) == ["candidate_v1_research"]

        with TestClient(main.app) as client:
            prediction_ids = []

            for symptom_text, codes, expected_status in (
                (
                    "I have tiredness and headache",
                    ["tiredness", "headache"],
                    "insufficient_information",
                ),
                (
                    "I have tiredness and chest pain",
                    ["tiredness", "chest_pain"],
                    "prediction_complete",
                ),
            ):
                response = client.post(
                    "/predict",
                    headers=headers,
                    json={
                        "age": 22,
                        "sex": "F",
                        "text": symptom_text,
                        "confirmed_codes": codes,
                    },
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["status"] == expected_status
                assert result["model_version"] == "candidate_v1_research"
                prediction_ids.append(uuid.UUID(result["prediction_id"]))

            before_rollback = history_snapshot(prediction_ids)

            # Roll back model selection, not the database migration.
            with SessionLocal.begin() as db:
                restored = activate_v2_model_version(db)
                assert restored.version == V2_MODEL_VERSION

            with SessionLocal() as db:
                assert active_versions(db) == [V2_MODEL_VERSION]
                candidate = db.get(ModelVersion, candidate_id)
                assert candidate.is_active is False

                columns = {
                    column["name"]
                    for column in inspect(db.connection()).get_columns(
                        "prediction_sessions"
                    )
                }
                assert "assessment" in columns

            assert history_snapshot(prediction_ids) == before_rollback

            # Candidate assessments remain readable after switching to V2.
            for prediction_id in prediction_ids:
                response = client.get(
                    f"/history/{prediction_id}",
                    headers=headers,
                )
                assert response.status_code == 200, response.text
                saved = before_rollback[str(prediction_id)]
                assert response.json()["assessment"] == saved["assessment"]
                assert response.json()["predictions"] == saved["predictions"]

            # Future predictions now use V2.
            response = client.post(
                "/predict",
                headers=headers,
                json={
                    "age": 22,
                    "sex": "F",
                    "text": "I have fever and cough",
                    "confirmed_codes": ["fever", "cough"],
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["model_version"] == V2_MODEL_VERSION
            assert result["feature_count"] == 66

            with SessionLocal() as db:
                saved = db.get(
                    PredictionSession,
                    uuid.UUID(result["prediction_id"]),
                )
                assert saved.assessment is None
                assert saved.model_version.version == V2_MODEL_VERSION

            assert history_snapshot(prediction_ids) == before_rollback

    finally:
        # Restore the test registry to its original state.
        with SessionLocal() as db:
            rows = db.scalars(select(ModelVersion)).all()
            for row in rows:
                row.is_active = previous_states.get(row.id, False)

            for row in rows:
                if row.id not in previous_states:
                    db.delete(row)

            db.commit()