"""Unit tests for the backend candidate assessment adapter."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.errors import ServiceNotReadyError
from app.services import candidate_prediction as service
from app.services.candidate_symptom_mapping import CandidateMappingInputError


@pytest.fixture
def context(monkeypatch):
    provenance = {
        "model_version": "candidate_v1_research",
        "policy_id": "candidate_v1_research_uncertainty_v1",
        "bundle_id": "test-bundle",
        "manifest_sha256": "a" * 64,
        "schema_id": "respiratory_candidate_v1",
        "schema_sha256": "b" * 64,
        "model_sha256": "c" * 64,
        "policy_sha256": "d" * 64,
    }
    feature_names = ["AGE", "SEX_M"] + [
        f"fixture_feature_{index}" for index in range(68)
    ]
    features = dict.fromkeys(feature_names, 0.0)
    features["AGE"] = 22.0

    mapped = {
        "feature_values": features,
        "confirmed_codes": ["cough", "fever"],
        "symptom_state": {
            "present": ["E_91", "E_201"],
            "absent": [],
            "unknown": [],
        },
        "reported_unit_count": 2,
        "safety_flags": [],
        "unsupported_symptoms": [],
        "clarification_required": [],
        "unmatched_wording": [],
        "has_unmatched_wording": False,
        "mapping_provenance": {
            "mapping_id": "backend_candidate_symptom_mapping_v1",
            "mapping_sha256": "e" * 64,
            "bundle_manifest_sha256": provenance["manifest_sha256"],
        },
    }
    raw = {
        "status": "prediction_available",
        "model_version": provenance["model_version"],
        "policy_id": provenance["policy_id"],
        "predicted_condition": "URTI",
        "model_score": 0.8123456789,
        "uncertain": False,
        "uncertainty_reasons": [],
        "reported_unit_count": 2,
        "probability_representation": "temperature_scaled",
        "prediction_performed": True,
        "safety_flags": [],
        "safety_review_required": False,
        "unsupported_symptoms": [],
        "unsupported_absent_concepts": [],
        "message": "A leading condition met the acceptance rules.",
    }

    runtime = SimpleNamespace(
        provenance=provenance,
        schema={"feature_names": feature_names},
        classes=("URTI", "Influenza"),
        predict=Mock(return_value=raw),
    )
    mapper = Mock(return_value=mapped)
    loader = Mock(return_value=runtime)
    enriched = [
        {
            "rank": 1,
            "condition": "URTI",
            "display_name": "Test display name",
            "model_confidence_percent": 81.23,
            "description": "Test description",
        }
    ]
    enrich = Mock(return_value=enriched)

    monkeypatch.setattr(service, "map_candidate_confirmation", mapper)
    monkeypatch.setattr(service, "load_candidate_runtime", loader)
    monkeypatch.setattr(service, "enrich_predictions", enrich)

    return SimpleNamespace(
        mapped=mapped,
        raw=raw,
        runtime=runtime,
        mapper=mapper,
        loader=loader,
        enrich=enrich,
        enriched=enriched,
        confirmation={"server_confirmation_fixture": True},
    )


def assess(context):
    return service.assess_candidate_confirmation(
        age=22,
        sex="F",
        confirmation=context.confirmation,
    )


def withhold(context, status, reason, *, performed=False):
    context.raw.update(
        status=status,
        predicted_condition=None,
        model_score=None,
        uncertain=True,
        uncertainty_reasons=[reason],
        prediction_performed=performed,
        message="No leading condition is returned.",
    )


def test_accepted_response_preserves_score_and_provenance(context):
    result = assess(context)
    assessment = result["assessment"]

    assert result["status"] == "prediction_complete"
    assert result["feature_count"] == 70
    assert result["predictions"] == context.enriched
    assert assessment["model_score"] == 0.8123456789
    assert assessment["contract_version"] == 1
    assert assessment["mapping_version"] == (
        "backend_candidate_symptom_mapping_v1"
    )

    for field in (
        "model_version",
        "policy_id",
        "bundle_id",
        "schema_id",
        "schema_sha256",
        "model_sha256",
        "policy_sha256",
    ):
        assert assessment[field] == context.runtime.provenance[field]

    assert assessment["bundle_manifest_sha256"] == (
        context.runtime.provenance["manifest_sha256"]
    )
    assert assessment["mapping_provenance"] == (
        context.mapped["mapping_provenance"]
    )
    assert assessment["score_note"] == service.SCORE_NOTE

    context.enrich.assert_called_once_with(
        [{"rank": 1, "condition": "URTI", "confidence_percent": 81.23}]
    )
    context.mapper.assert_called_once_with(
        22, "F", context.confirmation
    )
    context.runtime.predict.assert_called_once_with(
        context.mapped["feature_values"],
        safety_flags=[],
        unsupported_symptoms=[],
        include_diagnostics=False,
    )


@pytest.mark.parametrize("performed", [False, True])
def test_insufficient_information_never_enriches_a_condition(
    context, performed
):
    reason = (
        "below_score_threshold"
        if performed
        else "too_few_supported_reporting_units"
    )
    withhold(
        context,
        "insufficient_information",
        reason,
        performed=performed,
    )
    if not performed:
        context.mapped["reported_unit_count"] = 1
        context.raw["reported_unit_count"] = 1

    result = assess(context)

    assert result["status"] == "insufficient_information"
    assert result["predictions"] == []
    assert result["assessment"]["predicted_condition"] is None
    assert result["assessment"]["model_score"] is None
    assert result["assessment"]["prediction_performed"] is performed
    assert result["assessment"]["uncertainty_reasons"] == [reason]
    assert result["assessment"]["score_note"]
    context.enrich.assert_not_called()


def test_unsupported_evidence_and_clarification_are_preserved(context):
    withhold(
        context, "unsupported_input", "unsupported_symptoms_present"
    )
    context.mapped["unsupported_symptoms"] = ["muscle_aches"]
    context.raw["unsupported_symptoms"] = ["muscle_aches"]
    context.mapped["clarification_required"] = [
        {
            "code": "muscle_aches",
            "required_meaning": "Diffuse or widespread muscle pain.",
        }
    ]

    result = assess(context)

    assert result["status"] == "unsupported_input"
    assert result["predictions"] == []
    assert result["assessment"]["unsupported_symptoms"] == ["muscle_aches"]
    assert result["assessment"]["clarification_required"] == (
        context.mapped["clarification_required"]
    )
    context.enrich.assert_not_called()


def test_runtime_safety_status_translates_to_urgent_review(context):
    withhold(context, "safety_review_required", "safety_flags_present")
    context.mapped["safety_flags"] = ["blue_lips"]
    context.mapped["unsupported_symptoms"] = ["blue_lips"]
    context.raw.update(
        safety_flags=["blue_lips"],
        unsupported_symptoms=["blue_lips"],
        safety_review_required=True,
    )

    result = assess(context)

    assert result["status"] == "urgent_review"
    assert result["has_red_flag"] is True
    assert result["predictions"] == []
    assert result["assessment"]["status"] == "safety_review_required"
    assert result["assessment"]["prediction_performed"] is False
    context.enrich.assert_not_called()


def test_diagnostic_and_unapproved_fields_are_not_forwarded(context):
    withhold(context, "insufficient_information", "below_score_threshold")
    context.raw["diagnostics"] = {
        "ranked_conditions": [{"condition": "Hidden research leader"}]
    }
    context.raw["internal_debug"] = "Private runtime information"

    result = assess(context)

    assert "diagnostics" not in result["assessment"]
    assert "internal_debug" not in result["assessment"]
    assert "Hidden research leader" not in repr(result)
    assert result["predictions"] == []


def test_unmatched_wording_is_preserved_without_mutating_inputs(context):
    context.mapped["unmatched_wording"] = [
        {"unmatched_text": "sneezing", "suggestions": []}
    ]
    context.mapped["has_unmatched_wording"] = True
    original_mapped = deepcopy(context.mapped)
    original_raw = deepcopy(context.raw)
    original_confirmation = deepcopy(context.confirmation)

    result = assess(context)

    assert context.mapped == original_mapped
    assert context.raw == original_raw
    assert context.confirmation == original_confirmation
    assert result["assessment"]["has_unmatched_wording"] is True
    assert result["assessment"]["unmatched_wording"] == (
        original_mapped["unmatched_wording"]
    )

    result["assessment"]["unmatched_wording"][0]["suggestions"].append(
        "changed after return"
    )
    assert context.mapped == original_mapped


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "unknown_status"),
        ("model_version", "another_model"),
        ("policy_id", "another_policy"),
        ("reported_unit_count", 3),
        ("reported_unit_count", True),
        ("uncertain", "false"),
        ("prediction_performed", False),
        ("safety_review_required", True),
        ("predicted_condition", "Unknown condition"),
        ("model_score", True),
        ("model_score", float("nan")),
        ("model_score", float("inf")),
        ("model_score", -0.01),
        ("model_score", 1.01),
        ("uncertainty_reasons", ["unexpected_reason"]),
        ("unsupported_absent_concepts", ["E_91"]),
        ("probability_representation", "unknown"),
        ("message", ""),
    ],
)
def test_invalid_runtime_response_is_an_internal_error(
    context, field, value
):
    context.raw[field] = value

    with pytest.raises(service.CandidateAssessmentError):
        assess(context)

    context.enrich.assert_not_called()


def test_incomplete_runtime_response_is_rejected(context):
    del context.raw["model_score"]

    with pytest.raises(service.CandidateAssessmentError):
        assess(context)


def test_withheld_response_cannot_contain_a_hidden_condition(context):
    withhold(context, "insufficient_information", "below_score_threshold")
    context.raw["predicted_condition"] = "URTI"

    with pytest.raises(service.CandidateAssessmentError):
        assess(context)

    context.enrich.assert_not_called()


def test_bundle_mismatch_stops_before_prediction(context):
    context.mapped["mapping_provenance"]["bundle_manifest_sha256"] = (
        "f" * 64
    )

    with pytest.raises(service.CandidateAssessmentError):
        assess(context)

    context.runtime.predict.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        CandidateMappingInputError("Invalid confirmation."),
        ServiceNotReadyError("Mapping unavailable."),
    ],
)
def test_mapping_errors_preserve_their_exception_type(context, error):
    context.mapper.side_effect = error

    with pytest.raises(type(error)) as caught:
        assess(context)

    assert caught.value is error
    context.runtime.predict.assert_not_called()


def test_loader_unavailability_remains_service_not_ready(context):
    context.loader.side_effect = ServiceNotReadyError("Runtime unavailable.")

    with pytest.raises(ServiceNotReadyError):
        assess(context)

    context.runtime.predict.assert_not_called()


@pytest.mark.parametrize("stage", ["prediction", "enrichment"])
def test_internal_value_errors_do_not_become_user_input_errors(
    context, stage
):
    error = ValueError("Internal failure details.")
    target = (
        context.runtime.predict
        if stage == "prediction"
        else context.enrich
    )
    target.side_effect = error

    with pytest.raises(service.CandidateAssessmentError) as caught:
        assess(context)

    assert caught.value.__cause__ is error
    assert not isinstance(caught.value, ValueError)
    assert str(caught.value) == (
        "Candidate assessment could not be completed."
    )