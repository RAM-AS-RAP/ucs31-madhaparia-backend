"""HTTP routing tests with mocked candidate inference and persistence."""

import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.errors import ServiceNotReadyError
from app.services.candidate_prediction import CandidateAssessmentError
from app.services.candidate_symptom_mapping import CandidateMappingInputError


@pytest.fixture
def context(monkeypatch):
    db = Mock()
    user = SimpleNamespace(id=uuid.uuid4(), role="patient")
    registration = SimpleNamespace(
        id=uuid.uuid4(),
        version=main.CANDIDATE_MODEL_VERSION,
    )
    prediction_id = uuid.uuid4()

    confirmation = {
        "confirmed_codes": ["fever", "cough"],
        "model_features": ["E_91", "E_201"],
        "has_red_flag": False,
        "red_flag_symptoms": [],
        "unmatched_wording": [],
    }

    candidate = {
        "status": "prediction_complete",
        "model_version": main.CANDIDATE_MODEL_VERSION,
        "feature_count": 70,
        "confirmed_codes": ["cough", "fever"],
        "model_features": ["E_91", "E_201"],
        "has_red_flag": False,
        "predictions": [
            {
                "rank": 1,
                "condition": "URTI",
                "display_name": "Test condition",
                "model_confidence_percent": 81.23,
                "description": "Test description.",
            }
        ],
        "assessment": {
            "contract_version": 1,
            "status": "prediction_available",
            "model_version": main.CANDIDATE_MODEL_VERSION,
            "policy_id": "candidate_v1_research_uncertainty_v1",
            "predicted_condition": "URTI",
            "model_score": 0.8123456789,
            "uncertain": False,
            "uncertainty_reasons": [],
            "reported_unit_count": 2,
            "prediction_performed": True,
            "safety_flags": [],
            "unsupported_symptoms": [],
            "score_note": "Research model score, not a diagnosis.",
        },
    }

    confirm = Mock(return_value=confirmation)
    active = Mock(return_value=registration)
    validate = Mock()
    assess = Mock(return_value=candidate)
    save = Mock(return_value=SimpleNamespace(id=prediction_id))

    monkeypatch.setattr(main, "confirm_symptoms", confirm)
    monkeypatch.setattr(main, "get_active_model_version", active)
    monkeypatch.setattr(main, "validate_candidate_registration", validate)
    monkeypatch.setattr(main, "assess_candidate_confirmation", assess)
    monkeypatch.setattr(main, "save_prediction_session", save)

    legacy_guards = []
    for name in (
        "predict_v2_single",
        "predict_top_conditions",
        "partition_v2_symptom_features",
        "enrich_predictions",
    ):
        guard = Mock(
            side_effect=AssertionError("Unexpected legacy prediction work.")
        )
        monkeypatch.setattr(main, name, guard)
        legacy_guards.append(guard)

    previous_overrides = main.app.dependency_overrides.copy()
    main.app.dependency_overrides[main.get_db] = lambda: db
    main.app.dependency_overrides[main.get_current_user] = lambda: user

    # Do not enter the client as a context manager: these routing tests
    # use mocked persistence and do not need the application lifespan.
    client = TestClient(main.app)

    try:
        yield SimpleNamespace(
            client=client,
            db=db,
            user=user,
            registration=registration,
            prediction_id=prediction_id,
            confirmation=confirmation,
            candidate=candidate,
            confirm=confirm,
            active=active,
            validate=validate,
            assess=assess,
            save=save,
        )
    finally:
        client.close()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous_overrides)

        for guard in legacy_guards:
            guard.assert_not_called()


def request_body(**overrides):
    body = {
        "age": 22,
        "sex": "F",
        "text": "I have fever and cough",
        "confirmed_codes": ["fever", "cough"],
    }
    body.update(overrides)
    return body


def test_candidate_response_and_saved_assessment_match(context):
    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 200
    data = response.json()

    assert data["prediction_id"] == str(context.prediction_id)
    assert data["status"] == "prediction_complete"
    assert data["feature_count"] == 70
    assert data["assessment"] == context.candidate["assessment"]
    assert data["predictions"] == context.candidate["predictions"]
    assert data["assessment"]["model_score"] == 0.8123456789
    assert data["confidence_explanation"] == (
        context.candidate["assessment"]["score_note"]
    )

    context.validate.assert_called_once_with(context.registration)
    context.assess.assert_called_once_with(
        age=22,
        sex="F",
        confirmation=context.confirmation,
    )
    context.save.assert_called_once()

    saved = context.save.call_args.kwargs
    assert saved["user_id"] == context.user.id
    assert saved["model_version_id"] == context.registration.id
    assert saved["assessment"] == data["assessment"]
    assert saved["predictions"] == data["predictions"]
    assert saved["confirmed_codes"] == data["confirmed_codes"]
    assert saved["model_features"] == data["model_features"]


@pytest.mark.parametrize(
    ("assessment_status", "backend_status", "reason", "performed"),
    [
        (
            "insufficient_information",
            "insufficient_information",
            "too_few_supported_reporting_units",
            False,
        ),
        (
            "insufficient_information",
            "insufficient_information",
            "below_score_threshold",
            True,
        ),
        (
            "unsupported_input",
            "unsupported_input",
            "unsupported_symptoms_present",
            False,
        ),
        (
            "safety_review_required",
            "urgent_review",
            "safety_flags_present",
            False,
        ),
    ],
)
def test_withheld_assessments_return_200_and_are_saved(
    context, assessment_status, backend_status, reason, performed
):
    context.candidate.update(
        status=backend_status,
        predictions=[],
        has_red_flag=backend_status == "urgent_review",
    )
    context.candidate["assessment"].update(
        status=assessment_status,
        predicted_condition=None,
        model_score=None,
        uncertain=True,
        uncertainty_reasons=[reason],
        prediction_performed=performed,
    )

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == backend_status
    assert data["predictions"] == []
    assert data["assessment"]["predicted_condition"] is None
    assert data["assessment"]["model_score"] is None
    assert data["assessment"]["prediction_performed"] is performed
    assert "URTI" not in response.text

    saved = context.save.call_args.kwargs
    assert saved["status"] == backend_status
    assert saved["predictions"] == []
    assert saved["assessment"] == data["assessment"]
    assert saved["model_version_id"] == context.registration.id


def test_empty_confirmation_reaches_candidate_assessment(context):
    context.confirmation.update(
        confirmed_codes=[],
        model_features=[],
    )
    context.candidate.update(
        status="insufficient_information",
        confirmed_codes=[],
        model_features=[],
        predictions=[],
    )
    context.candidate["assessment"].update(
        status="insufficient_information",
        predicted_condition=None,
        model_score=None,
        uncertain=True,
        uncertainty_reasons=["too_few_supported_reporting_units"],
        reported_unit_count=0,
        prediction_performed=False,
    )

    response = context.client.post(
        "/predict",
        json=request_body(confirmed_codes=[]),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_information"
    assert response.json()["assessment"]["reported_unit_count"] == 0
    context.assess.assert_called_once()
    context.save.assert_called_once()


@pytest.mark.parametrize(
    "age",
    [True, False, 22.0, 22.5, "22", -1, 121, None],
)
def test_invalid_age_is_rejected_before_confirmation(context, age):
    response = context.client.post(
        "/predict",
        json=request_body(age=age),
    )

    assert response.status_code == 422
    context.confirm.assert_not_called()
    context.assess.assert_not_called()
    context.save.assert_not_called()


@pytest.mark.parametrize("sex", ["X", "female", "", None])
def test_invalid_sex_is_rejected(context, sex):
    response = context.client.post(
        "/predict",
        json=request_body(sex=sex),
    )

    assert response.status_code == 422
    context.confirm.assert_not_called()
    context.save.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("explicitly_absent_features", ["E_91"]),
        ("feature_values", {"AGE": 22}),
        ("model_version", "1.0"),
        ("min_score", 0.0),
    ],
)
def test_unimplemented_or_client_controlled_fields_are_rejected(
    context, field, value
):
    body = request_body()
    body[field] = value

    response = context.client.post("/predict", json=body)

    assert response.status_code == 422
    context.confirm.assert_not_called()
    context.save.assert_not_called()


def test_authentication_is_required(context):
    main.app.dependency_overrides.pop(main.get_current_user)

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 401
    context.confirm.assert_not_called()
    context.save.assert_not_called()


def test_invalid_confirmation_is_not_saved(context):
    context.confirm.side_effect = ValueError("Invalid confirmed symptom.")

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 400
    context.active.assert_not_called()
    context.assess.assert_not_called()
    context.save.assert_not_called()


def test_invalid_candidate_mapping_is_not_saved(context):
    context.assess.side_effect = CandidateMappingInputError(
        "Confirmed codes and recognised symptom records disagree."
    )

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 400
    context.save.assert_not_called()


@pytest.mark.parametrize("stage", ["validate", "assess"])
def test_candidate_unavailability_returns_sanitized_503(context, stage):
    getattr(context, stage).side_effect = ServiceNotReadyError(
        "Private artifact path and internal details."
    )

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The candidate assessment service is unavailable."
    )
    assert "Private" not in response.text
    context.save.assert_not_called()

    if stage == "validate":
        context.assess.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        CandidateAssessmentError("Internal inference details."),
        ValueError("Unexpected internal value failure."),
    ],
)
def test_inference_failure_returns_500_without_saving(context, error):
    context.assess.side_effect = error

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The candidate assessment could not be completed."
    )
    context.save.assert_not_called()
    context.db.rollback.assert_called_once()


def test_persistence_failure_does_not_return_success(context):
    context.save.side_effect = RuntimeError("Private database details.")

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "The candidate assessment could not be completed."
    )
    assert "prediction_id" not in response.json()
    assert "Private" not in response.text
    context.db.rollback.assert_called_once()


def test_unknown_active_model_does_not_fall_back(context):
    context.registration.version = "unknown_model"

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 503
    context.validate.assert_not_called()
    context.assess.assert_not_called()
    context.save.assert_not_called()


def test_response_building_does_not_modify_service_result(context):
    original = deepcopy(context.candidate)

    response = context.client.post("/predict", json=request_body())

    assert response.status_code == 200
    assert context.candidate == original