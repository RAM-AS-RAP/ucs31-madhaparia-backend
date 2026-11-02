"""Register the verified candidate without changing model activation."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ServiceNotReadyError
from app.db.models import ModelVersion
from app.ml import candidate_loader


CANDIDATE_MODEL_NAME = "Respiratory Random Forest Candidate"


class CandidateRegistrationConflictError(ValueError):
    """An existing version contains different registration metadata."""


def build_candidate_registration_values() -> dict:
    """Build database metadata from verified bundle bytes."""

    runtime = candidate_loader.load_candidate_runtime()

    try:
        from respiratory_candidate_runtime.integrity import (
            decode_json_bytes,
            read_verified_bundle,
        )

        manifest, payloads = read_verified_bundle(
            candidate_loader.CANDIDATE_BUNDLE_DIRECTORY,
            runtime.provenance["manifest_sha256"],
        )

        def document(name):
            return decode_json_bytes(payloads[name], name)

        training = document("provenance/training_manifest.json")
        selection = document("provenance/model_selection.json")
        uncertainty = document("provenance/uncertainty_evaluation.json")
        uncertainty_decision = document(
            "provenance/uncertainty_decision.json"
        )
        final = document("provenance/final_evaluation.json")
        final_decision = document("provenance/final_decision.json")
        final_verification = document("provenance/final_verification.json")
        policy = document("policy.json")

        if training["model_sha256"] != runtime.provenance["model_sha256"]:
            raise ValueError("Training provenance refers to another model.")

        if training["classes"] != list(runtime.classes):
            raise ValueError("Training class order differs from the runtime.")

        if training["feature_names"] != runtime.schema["feature_names"]:
            raise ValueError("Training feature order differs from the runtime.")

        if selection["selected_candidate"] != "rf_group_weighted":
            raise ValueError("Unexpected selected candidate.")

        if uncertainty["partition"] != "uncertainty_evaluation":
            raise ValueError("Unexpected reserved evaluation partition.")

        if uncertainty["source_groups"] != uncertainty_decision["source_groups"]:
            raise ValueError("Reserved evaluation counts disagree.")

        if uncertainty_decision["policy_sha256"] != runtime.provenance["policy_sha256"]:
            raise ValueError("Reserved evaluation refers to another policy.")

        if final["cohort_counts"] != final_verification["cohort_counts"]:
            raise ValueError("Final evaluation counts disagree.")

        if final_verification["status"] != "final_metrics_verified_against_saved_predictions":
            raise ValueError("Final metric verification is incomplete.")

        if not uncertainty_decision["research_targets_met_on_all_required_views"]:
            raise ValueError("Reserved uncertainty targets were not met.")

        if final_decision["research_target_assessment"] != "met":
            raise ValueError("Final research targets were not met.")

        fitting_groups = training["fitting_summary"]["source_groups"]
        fitting_observations = training["fitting_summary"]["observations"]
        validation_groups = uncertainty["source_groups"]
        test_groups = final["cohort_counts"]["primary"]

        for count in (
            fitting_groups,
            fitting_observations,
            validation_groups,
            test_groups,
        ):
            if type(count) is not int or count <= 0:
                raise ValueError("Invalid recorded sample count.")

        provenance_files = {
            name: entry["sha256"]
            for name, entry in manifest["files"].items()
            if name.startswith("provenance/")
        }

        return {
            "model_name": CANDIDATE_MODEL_NAME,
            "version": candidate_loader.CANDIDATE_MODEL_VERSION,
            "purpose": (
                "Evaluated respiratory research candidate; "
                "initial registration is inactive."
            ),
            "artifact_filename": "candidate_v1/bundle/model.joblib",
            "feature_count": len(runtime.schema["feature_names"]),
            "class_count": len(runtime.classes),
            "training_samples": fitting_groups,
            "validation_samples": validation_groups,
            "test_samples": test_groups,
            "parameters": {
                "estimator": "RandomForestClassifier",
                "selected_candidate": "rf_group_weighted",
                "random_forest": training["random_forest_parameters"],
                "fitting_observations": fitting_observations,
                "sample_count_definitions": {
                    "training_samples": "unique fitting source groups",
                    "validation_samples": (
                        "reserved uncertainty-evaluation source groups"
                    ),
                    "test_samples": "primary official-test source groups",
                },
                "runtime_provenance": runtime.provenance,
                "provenance_file_sha256": provenance_files,
                "uncertainty_policy": {
                    name: policy[name]
                    for name in (
                        "policy_id",
                        "mode",
                        "probability_representation",
                        "temperature",
                        "min_reporting_units",
                        "min_score",
                        "min_margin",
                    )
                },
                "clinical_validation": False,
                "deployment_approved": False,
            },
            "validation_metrics": {
                "evaluated": True,
                "sample_count_scope": "uncertainty_evaluation",
                "source_groups": validation_groups,
                "uncertainty_evaluation_decision": uncertainty_decision,
                "model_selection_summary": selection,
                "interpretation": (
                    "Registry summary of simulated-data evaluations. "
                    "Model selection and reserved uncertainty evaluation "
                    "use different cohorts."
                ),
            },
            "test_metrics": {
                "evaluated": True,
                "sample_count_scope": "primary",
                "cohort_counts": final["cohort_counts"],
                "evaluation_decision": final_decision,
                "verification_status": final_verification["status"],
                "recorded_summary_sha256": {
                    name: final["output_sha256"][name]
                    for name in (
                        "classification_summary.csv",
                        "candidate_probability_summary.csv",
                        "policy_summary.csv",
                    )
                },
                "clinical_validation": False,
                "historical_test_exposure_cannot_be_ruled_out": True,
            },
        }

    except Exception as error:
        raise ServiceNotReadyError(
            "Verified candidate registration metadata is unavailable."
        ) from error


def register_candidate_model_version(db: Session) -> ModelVersion:
    """Insert an inactive candidate or return an identical existing record.

    Does not commit. The caller owns the transaction.
    Existing activation status is preserved.
    Conflicting metadata requires explicit review rather than replacement.
    """

    values = build_candidate_registration_values()

    with db.no_autoflush:
        existing = db.scalar(
            select(ModelVersion)
            .where(
                ModelVersion.model_name == values["model_name"],
                ModelVersion.version == values["version"],
            )
            .with_for_update()
        )

    if existing is not None:
        conflicting_fields = [
            field
            for field, value in values.items()
            if getattr(existing, field) != value
        ]
        if conflicting_fields:
            raise CandidateRegistrationConflictError(
                "Existing candidate metadata differs in: "
                + ", ".join(sorted(conflicting_fields))
                + ". No metadata or activation status was changed."
            )
        return existing

    record = ModelVersion(**values, is_active=False)
    db.add(record)
    db.flush()
    return record