"""Assessment storage checks using the isolated PostgreSQL test schema."""

import uuid
from copy import deepcopy

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.db.models import PredictionSession, User
from app.services.history import save_prediction_session


@pytest.fixture
def patient_id():
    with SessionLocal() as db:
        patient = User(
            email=f"assessment-storage-{uuid.uuid4().hex}@example.com",
            password_hash="test-only-not-a-real-password",
            role="patient",
        )
        db.add(patient)
        db.commit()
        return patient.id


def save_example(db, patient_id, **overrides):
    values = {
        "user_id": patient_id,
        "age": 22,
        "sex": "F",
        "input_text": "I have fever and cough",
        "confirmed_codes": ["fever", "cough"],
        "model_features": ["E_91", "E_201"],
        "status": "prediction_complete",
        "has_red_flag": False,
        "predictions": [],
    }
    values.update(overrides)
    return save_prediction_session(db, **values)


def test_migration_creates_nullable_jsonb_column():
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("prediction_sessions")
    }
    assessment = columns["assessment"]

    assert isinstance(assessment["type"], JSONB)
    assert assessment["nullable"] is True
    assert assessment["default"] is None


@pytest.mark.parametrize("explicit_none", [False, True])
def test_legacy_call_stores_sql_null(patient_id, explicit_none):
    old_predictions = [
        {
            "rank": 1,
            "condition": "Historical condition",
            "model_confidence_percent": 37.5,
            "description": "Description saved at prediction time.",
        }
    ]
    extra = {"assessment": None} if explicit_none else {}

    with SessionLocal() as db:
        saved = save_example(
            db,
            patient_id,
            predictions=old_predictions,
            **extra,
        )
        saved_id = saved.id

    with SessionLocal() as db:
        restored = db.get(PredictionSession, saved_id)

        assert restored.assessment is None
        assert restored.predictions == old_predictions
        assert restored.status == "prediction_complete"

        is_sql_null = db.scalar(
            text(
                "SELECT assessment IS NULL "
                "FROM prediction_sessions WHERE id = :id"
            ),
            {"id": saved_id},
        )
        assert is_sql_null is True


@pytest.mark.parametrize(
    ("assessment_status", "backend_status"),
    [
        ("prediction_available", "prediction_complete"),
        ("insufficient_information", "insufficient_information"),
        ("unsupported_input", "unsupported_input"),
        ("safety_review_required", "urgent_review"),
    ],
)
def test_assessment_snapshot_round_trip(
    patient_id,
    assessment_status,
    backend_status,
):
    # Storage fixtures, not outputs from a model or API contract validator.
    accepted = assessment_status == "prediction_available"
    assessment = {
        "contract_version": 1,
        "status": assessment_status,
        "model_version": "candidate_v1_research",
        "policy_id": "candidate_v1_research_uncertainty_v1",
        "predicted_condition": "URTI" if accepted else None,
        "model_score": 0.8123456789 if accepted else None,
        "uncertain": not accepted,
        "uncertainty_reasons": [] if accepted else ["test_reason"],
        "reported_unit_count": 2,
        "prediction_performed": accepted,
        "safety_flags": (
            ["test_safety_flag"]
            if assessment_status == "safety_review_required"
            else []
        ),
    }
    predictions = (
        [{"rank": 1, "condition": "URTI", "model_confidence_percent": 81.23}]
        if accepted
        else []
    )
    expected_assessment = deepcopy(assessment)
    expected_predictions = deepcopy(predictions)

    with SessionLocal() as db:
        saved = save_example(
            db,
            patient_id,
            status=backend_status,
            has_red_flag=assessment_status == "safety_review_required",
            predictions=predictions,
            assessment=assessment,
        )
        saved_id = saved.id

        assessment["uncertainty_reasons"].append("changed_after_save")
        predictions.append({"condition": "changed_after_save"})

        assert saved.assessment == expected_assessment
        assert saved.predictions == expected_predictions

    with SessionLocal() as db:
        restored = db.get(PredictionSession, saved_id)

        assert restored.user_id == patient_id
        assert restored.status == backend_status
        assert restored.assessment == expected_assessment
        assert restored.predictions == expected_predictions


def test_failed_save_rolls_back_session_and_assessment(patient_id):
    marker = f"failed-assessment-{uuid.uuid4().hex}"

    with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            save_example(
                db,
                patient_id,
                input_text=marker,
                model_version_id=uuid.uuid4(),  # Missing foreign-key target.
                assessment={
                    "contract_version": 1,
                    "status": "insufficient_information",
                },
            )

        # The service must leave the session usable after rollback.
        assert db.scalar(text("SELECT 1")) == 1
        assert db.scalar(
            select(PredictionSession.id).where(
                PredictionSession.input_text == marker
            )
        ) is None

    with SessionLocal() as db:
        assert db.scalar(
            select(PredictionSession.id).where(
                PredictionSession.input_text == marker
            )
        ) is None