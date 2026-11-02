"""Candidate assessment adapter for server-confirmed symptoms.

The API must perform its independent red-flag check first.
This service does not select or activate a database model,
save history, or change the frozen inference policy.
"""

import math
from copy import deepcopy

from app.core.errors import ServiceNotReadyError
from app.ml.candidate_loader import load_candidate_runtime
from app.services.candidate_symptom_mapping import (
    CandidateMappingInputError,
    map_candidate_confirmation,
)
from app.services.health_guidance import enrich_predictions


STATUS_TRANSLATION = {
    "prediction_available": "prediction_complete",
    "insufficient_information": "insufficient_information",
    "unsupported_input": "unsupported_input",
    "safety_review_required": "urgent_review",
}

SCORE_NOTE = (
    "Model scores were evaluated on simulated respiratory-condition "
    "data. They are not clinically validated individual disease risks."
)

PUBLIC_RUNTIME_FIELDS = (
    "status",
    "model_version",
    "policy_id",
    "predicted_condition",
    "model_score",
    "uncertain",
    "uncertainty_reasons",
    "reported_unit_count",
    "probability_representation",
    "prediction_performed",
    "safety_flags",
    "unsupported_symptoms",
    "unsupported_absent_concepts",
    "message",
)


class CandidateAssessmentError(RuntimeError):
    """An internal assessment failure, not an invalid user request."""


def _check(condition, message):
    if not condition:
        raise CandidateAssessmentError(message)


def _validate_result(result, runtime, mapped):
    _check(isinstance(result, dict), "Invalid candidate response.")

    _check(
        all(field in result for field in PUBLIC_RUNTIME_FIELDS),
        "Incomplete candidate response.",
    )

    status = result["status"]
    _check(
        isinstance(status, str) and status in STATUS_TRANSLATION,
        "Unknown candidate assessment status.",
    )

    provenance = runtime.provenance
    for field in ("model_version", "policy_id"):
        _check(
            result[field] == provenance[field],
            "Candidate response identity mismatch.",
        )

    _check(
        type(result["reported_unit_count"]) is int
        and result["reported_unit_count"] == mapped["reported_unit_count"],
        "Candidate reporting-unit count mismatch.",
    )

    for field in ("uncertain", "prediction_performed", "safety_review_required"):
        _check(
            type(result.get(field)) is bool,
            "Invalid candidate boolean field.",
        )

    for field in (
        "uncertainty_reasons",
        "safety_flags",
        "unsupported_symptoms",
        "unsupported_absent_concepts",
    ):
        values = result[field]
        _check(
            isinstance(values, list)
            and all(isinstance(value, str) and value for value in values),
            "Invalid candidate reason or context list.",
        )
        _check(
            len(values) == len(set(values)),
            "Duplicate candidate reason or context code.",
        )

    for field in ("safety_flags", "unsupported_symptoms"):
        _check(
            set(result[field]) == set(mapped[field]),
            "Candidate context differs from confirmed mapping.",
        )

    _check(
        result["unsupported_absent_concepts"] == [],
        "Unexpected negative evidence in confirmed-symptom assessment.",
    )
    _check(
        result["safety_review_required"] == bool(result["safety_flags"]),
        "Inconsistent candidate safety response.",
    )

    _check(
        isinstance(result["message"], str) and result["message"].strip(),
        "Missing candidate assessment message.",
    )
    _check(
        result["probability_representation"]
        in ("original", "temperature_scaled"),
        "Unknown candidate probability representation.",
    )

    if result["safety_flags"]:
        _check(
            status == "safety_review_required"
            and result["prediction_performed"] is False,
            "Safety evidence did not block candidate scoring.",
        )
    elif result["unsupported_symptoms"]:
        _check(
            status == "unsupported_input"
            and result["prediction_performed"] is False,
            "Unsupported evidence did not block candidate scoring.",
        )
    else:
        _check(
            status in ("prediction_available", "insufficient_information"),
            "Unexpected candidate status for supported evidence.",
        )

    accepted = status == "prediction_available"

    if accepted:
        score = result["model_score"]
        _check(
            result["predicted_condition"] in runtime.classes,
            "Candidate returned an unknown condition.",
        )
        _check(
            type(score) in (int, float)
            and math.isfinite(score)
            and 0 <= score <= 1,
            "Candidate returned an invalid score.",
        )
        _check(
            result["uncertain"] is False
            and result["uncertainty_reasons"] == []
            and result["prediction_performed"] is True,
            "Inconsistent accepted candidate response.",
        )
    else:
        _check(
            result["predicted_condition"] is None
            and result["model_score"] is None
            and result["uncertain"] is True
            and bool(result["uncertainty_reasons"]),
            "Inconsistent withheld candidate response.",
        )


def assess_candidate_confirmation(*, age, sex, confirmation):
    """Assess a confirmation produced by the server confirmation service."""

    try:
        mapped = map_candidate_confirmation(age, sex, confirmation)
        runtime = load_candidate_runtime()
        provenance = runtime.provenance

        _check(
            mapped["mapping_provenance"]["bundle_manifest_sha256"]
            == provenance["manifest_sha256"],
            "Mapping and runtime bundle identities differ.",
        )

        result = runtime.predict(
            mapped["feature_values"],
            safety_flags=mapped["safety_flags"],
            unsupported_symptoms=mapped["unsupported_symptoms"],
            include_diagnostics=False,
        )

        _validate_result(result, runtime, mapped)

        # Forward only approved public fields, never diagnostic rankings.
        assessment = {
            field: deepcopy(result[field])
            for field in PUBLIC_RUNTIME_FIELDS
        }
        assessment.update(
            {
                "contract_version": 1,
                "bundle_id": provenance["bundle_id"],
                "bundle_manifest_sha256": provenance["manifest_sha256"],
                "schema_id": provenance["schema_id"],
                "schema_sha256": provenance["schema_sha256"],
                "model_sha256": provenance["model_sha256"],
                "policy_sha256": provenance["policy_sha256"],
                "mapping_version": mapped["mapping_provenance"]["mapping_id"],
                "mapping_provenance": deepcopy(mapped["mapping_provenance"]),
                "score_note": SCORE_NOTE,
                "safety_review_required": result["safety_review_required"],
                "clarification_required": deepcopy(
                    mapped["clarification_required"]
                ),
                "unmatched_wording": deepcopy(mapped["unmatched_wording"]),
                "has_unmatched_wording": mapped["has_unmatched_wording"],
            }
        )

        predictions = []
        if result["status"] == "prediction_available":
            predictions = enrich_predictions(
                [
                    {
                        "rank": 1,
                        "condition": result["predicted_condition"],
                        "confidence_percent": round(
                            result["model_score"] * 100, 2
                        ),
                    }
                ]
            )

        return {
            "status": STATUS_TRANSLATION[result["status"]],
            "model_version": provenance["model_version"],
            "feature_count": len(runtime.schema["feature_names"]),
            "confirmed_codes": deepcopy(mapped["confirmed_codes"]),
            "model_features": list(mapped["symptom_state"]["present"]),
            "has_red_flag": result["safety_review_required"],
            "predictions": predictions,
            "assessment": assessment,
        }

    except (CandidateMappingInputError, ServiceNotReadyError):
        raise
    except CandidateAssessmentError:
        raise
    except Exception as error:
        # Internal failures must not become user-input errors.
        raise CandidateAssessmentError(
            "Candidate assessment could not be completed."
        ) from error


def validate_candidate_registration(record):
    """Require the selected database record to match the verified runtime."""

    try:
        from app.db.register_candidate_model_version import (
            CANDIDATE_MODEL_NAME,
        )

        runtime = load_candidate_runtime()

        matches = (
            record.is_active is True
            and record.model_name == CANDIDATE_MODEL_NAME
            and record.version == runtime.provenance["model_version"]
            and record.artifact_filename == "candidate_v1/bundle/model.joblib"
            and record.feature_count == len(runtime.schema["feature_names"])
            and record.class_count == len(runtime.classes)
            and isinstance(record.parameters, dict)
            and record.parameters.get("runtime_provenance")
            == runtime.provenance
        )

        if not matches:
            raise ServiceNotReadyError(
                "Candidate registration does not match the verified runtime."
            )

    except ServiceNotReadyError:
        raise
    except Exception as error:
        raise ServiceNotReadyError(
            "Candidate registration could not be verified."
        ) from error


def build_independent_safety_assessment(confirmation):
    """Describe the existing safety route without loading any model."""

    safety_flags = sorted({
        item["code"]
        for item in confirmation["red_flag_symptoms"]
    })

    return {
        "contract_version": 1,
        "status": "safety_review_required",
        "model_version": None,
        "policy_id": None,
        "bundle_id": None,
        "bundle_manifest_sha256": None,
        "schema_id": None,
        "schema_sha256": None,
        "model_sha256": None,
        "policy_sha256": None,
        "mapping_version": None,
        "mapping_provenance": None,
        "predicted_condition": None,
        "model_score": None,
        "uncertain": True,
        "uncertainty_reasons": ["safety_flags_present"],
        "reported_unit_count": None,
        "probability_representation": None,
        "prediction_performed": False,
        "safety_flags": safety_flags,
        "safety_review_required": True,
        "unsupported_symptoms": [],
        "unsupported_absent_concepts": [],
        "clarification_required": [],
        "unmatched_wording": deepcopy(
            confirmation.get("unmatched_wording", [])
        ),
        "has_unmatched_wording": bool(
            confirmation.get("unmatched_wording", [])
        ),
        "message": (
            "Confirmed safety flags require review. "
            "No model prediction was performed."
        ),
        "score_note": (
            "No model score was calculated for this safety response."
        ),
    }