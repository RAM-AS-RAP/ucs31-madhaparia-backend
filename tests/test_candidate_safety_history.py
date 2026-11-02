"""Safety bypass and saved-assessment history response tests."""

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app.main as main
from app.services import candidate_prediction as service


def test_independent_safety_bypasses_all_model_work(monkeypatch):
    confirmation = {
        "confirmed_codes": ["blue_lips"],
        "model_features": [],
        "has_red_flag": True,
        "red_flag_symptoms": [{"code": "blue_lips"}],
        "unmatched_wording": [],
    }
    monkeypatch.setattr(
        main, "confirm_symptoms", Mock(return_value=confirmation)
    )

    guards = []
    for module, name in (
        (main, "get_active_model_version"),
        (main, "validate_candidate_registration"),
        (main, "assess_candidate_confirmation"),
        (main, "predict_v2_single"),
        (main, "predict_top_conditions"),
        (service, "load_candidate_runtime"),
    ):
        guard = Mock(side_effect=AssertionError("Model work was attempted."))
        monkeypatch.setattr(module, name, guard)
        guards.append(guard)

    saved = Mock(return_value=SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(main, "save_prediction_session", saved)

    result = main.predict_conditions(
        request=main.PredictionRequest(
            age=22,
            sex="F",
            text="My lips are blue",
            confirmed_codes=["blue_lips"],
        ),
        db=Mock(),
        current_user=SimpleNamespace(id=uuid.uuid4()),
    )

    assessment = result["assessment"]
    assert result["status"] == "urgent_review"
    assert result["predictions"] == []
    assert assessment["status"] == "safety_review_required"
    assert assessment["prediction_performed"] is False
    assert assessment["safety_flags"] == ["blue_lips"]
    assert assessment["score_note"]

    for field in (
        "model_version",
        "policy_id",
        "bundle_id",
        "bundle_manifest_sha256",
        "schema_id",
        "schema_sha256",
        "model_sha256",
        "policy_sha256",
        "mapping_version",
        "mapping_provenance",
        "predicted_condition",
        "model_score",
        "reported_unit_count",
        "probability_representation",
    ):
        assert assessment[field] is None

    assert saved.call_args.kwargs["assessment"] == assessment
    assert saved.call_args.kwargs["model_version_id"] is None
    assert saved.call_args.kwargs["predictions"] == []

    for guard in guards:
        guard.assert_not_called()


@pytest.mark.parametrize("has_assessment", [False, True])
@pytest.mark.parametrize(
    ("endpoint", "reader", "is_list", "is_admin"),
    [
        ("get_patient_history", "list_prediction_sessions", True, False),
        ("get_admin_history", "list_all_prediction_sessions", True, True),
        ("get_patient_history_item", "get_prediction_session", False, False),
        ("get_admin_history_item", "get_any_prediction_session", False, True),
    ],
)
def test_history_returns_saved_snapshot(
    monkeypatch, has_assessment, endpoint, reader, is_list, is_admin
):
    owner_id = uuid.uuid4()
    assessment = (
        {
            "contract_version": 1,
            "status": "insufficient_information",
            "model_version": "saved-model-version",
            "policy_id": "saved-policy-id",
            "model_sha256": "a" * 64,
            "predicted_condition": None,
            "model_score": None,
            "uncertainty_reasons": ["below_score_threshold"],
        }
        if has_assessment
        else None
    )
    predictions = (
        []
        if has_assessment
        else [
            {
                "condition": "Historical label",
                "description": "Original saved description.",
            }
        ]
    )
    record = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=owner_id,
        age=22,
        sex="F",
        input_text="Historical symptom text",
        confirmed_codes=["fever", "cough"],
        status=(
            "insufficient_information"
            if has_assessment
            else "prediction_complete"
        ),
        has_red_flag=False,
        predictions=deepcopy(predictions),
        assessment=deepcopy(assessment),
        created_at=datetime.now(timezone.utc),
    )
    lookup = Mock(return_value=[record] if is_list else record)
    monkeypatch.setattr(main, reader, lookup)

    for name in ("enrich_predictions", "assess_candidate_confirmation"):
        monkeypatch.setattr(
            main,
            name,
            Mock(side_effect=AssertionError("History must use saved values.")),
        )

    kwargs = {"db": Mock()}
    if is_list:
        kwargs["limit"] = 20
    else:
        kwargs["prediction_id"] = record.id

    if is_admin:
        kwargs["current_admin"] = SimpleNamespace(id=uuid.uuid4())
    else:
        kwargs["current_user"] = SimpleNamespace(id=owner_id)

    response = getattr(main, endpoint)(**kwargs)
    item = response["history"][0] if is_list else response

    assert item["assessment"] == assessment
    assert item["predictions"] == predictions
    assert item["status"] == record.status
    assert item["prediction_id"] == str(record.id)

    if not is_admin:
        assert lookup.call_args.kwargs["user_id"] == owner_id