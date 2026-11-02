"""Read-only pre-activation checks for the respiratory candidate.

Run from the backend root:
    python -B -m app.db.audit_candidate_readiness

Writes a local audit report after all checks pass.
Does not activate models, register records or save predictions.
"""

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import SessionLocal, check_schema
from app.db.models import ModelVersion, Symptom
from app.db.register_candidate_model_version import (
    build_candidate_registration_values,
)
from app.ml import candidate_loader
from app.services.candidate_symptom_mapping import (
    CATALOG_PATH,
    map_candidate_confirmation,
)


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "reports/candidate_readiness/readiness.json"
EXPECTED_REVISION = "d94a71f26c30"
EXPECTED_ACTIVE_VERSION = "v2_23class_prototype"
SCORE_TOLERANCE = 1e-12


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def compare_values(actual, expected, location="response"):
    """Require exact decisions and tolerate only tiny numeric differences."""

    if isinstance(expected, dict):
        require(isinstance(actual, dict), f"{location}: expected a dictionary.")
        require(set(actual) == set(expected), f"{location}: different fields.")
        for key in expected:
            compare_values(actual[key], expected[key], f"{location}.{key}")
        return

    if isinstance(expected, list):
        require(isinstance(actual, list), f"{location}: expected a list.")
        require(len(actual) == len(expected), f"{location}: different lengths.")
        for index, (first, second) in enumerate(zip(actual, expected)):
            compare_values(first, second, f"{location}[{index}]")
        return

    if type(expected) is float:
        require(
            type(actual) in (int, float)
            and math.isfinite(actual)
            and math.isfinite(expected)
            and math.isclose(
                actual,
                expected,
                rel_tol=0,
                abs_tol=SCORE_TOLERANCE,
            ),
            f"{location}: numerical mismatch.",
        )
        return

    require(
        type(actual) is type(expected) and actual == expected,
        f"{location}: value or type mismatch.",
    )


def verify_reference_cases(runtime, cases):
    from respiratory_candidate_runtime.errors import CandidateInputError

    require(len(cases) == 13, "Expected 13 reference cases.")
    require(
        len({case["case_id"] for case in cases}) == 13,
        "Reference case identifiers are duplicated.",
    )

    results = []

    for case in cases:
        case_id = case["case_id"]
        context = case["supplied_context"]

        if case["audit_status"] == "rejected_invalid_input":
            try:
                runtime.predict(
                    case["encoded_features"],
                    **context,
                    include_diagnostics=False,
                )
            except CandidateInputError as error:
                require(
                    str(error) == case["validation_error"],
                    f"{case_id}: input rejection differs from the reference.",
                )
            else:
                raise RuntimeError(f"{case_id}: invalid input was accepted.")

            status = "rejected_invalid_input"
        else:
            actual = runtime.predict(
                case["encoded_features"],
                **context,
                include_diagnostics=False,
            )
            compare_values(actual, case["public_response"], case_id)
            status = actual["status"]

        results.append({"case_id": case_id, "status": status})
        print(f"PASS: {case_id}")

    return results


def main():
    from respiratory_candidate_runtime.integrity import (
        decode_json_bytes,
        read_verified_bundle,
    )

    print("Checking schema and verified candidate resources...")
    check_schema()

    runtime = candidate_loader.load_candidate_runtime()
    expected_registration = build_candidate_registration_values()

    release = json.loads(
        candidate_loader.CANDIDATE_RELEASE_RECORD.read_text(
            encoding="utf-8"
        )
    )
    wheel_path = (
        candidate_loader.CANDIDATE_BUNDLE_DIRECTORY.parent
        / release["wheel_filename"]
    )
    wheel_digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    require(
        wheel_digest == release["wheel_sha256"],
        "Candidate wheel fingerprint differs from the trusted release.",
    )

    _, payloads = read_verified_bundle(
        candidate_loader.CANDIDATE_BUNDLE_DIRECTORY,
        runtime.provenance["manifest_sha256"],
    )
    reference_document = decode_json_bytes(
        payloads["reference_cases.json"],
        "reference_cases.json",
    )

    # This validates the local reviewed mapping and its catalogue fingerprint.
    empty_mapping = map_candidate_confirmation(
        22,
        "F",
        {
            "requires_confirmation": False,
            "confirmed_codes": [],
            "recognised_symptoms": [],
            "confirmed_symptoms": [],
            "has_red_flag": False,
            "unmatched_wording": [],
        },
    )
    catalogue = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    with SessionLocal() as db:
        # PostgreSQL enforces read-only access for this transaction.
        db.execute(
            text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
        )

        revisions = db.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all()
        require(
            revisions == [EXPECTED_REVISION],
            f"Unexpected database migration revision: {revisions}",
        )

        columns = {
            column["name"]: column
            for column in inspect(db.connection()).get_columns(
                "prediction_sessions"
            )
        }
        assessment_column = columns["assessment"]
        require(
            isinstance(assessment_column["type"], JSONB)
            and assessment_column["nullable"] is True,
            "The assessment column must be nullable JSONB.",
        )

        registrations = db.scalars(select(ModelVersion)).all()
        active = [row for row in registrations if row.is_active]
        require(
            len(active) == 1
            and active[0].version == EXPECTED_ACTIVE_VERSION,
            "Expected V2 to remain the sole active model.",
        )

        candidates = [
            row
            for row in registrations
            if row.model_name == expected_registration["model_name"]
            and row.version == expected_registration["version"]
        ]
        require(len(candidates) == 1, "Expected one candidate registration.")
        candidate = candidates[0]

        require(candidate.is_active is False, "Candidate is already active.")

        mismatches = [
            field
            for field, expected in expected_registration.items()
            if getattr(candidate, field) != expected
        ]
        require(
            not mismatches,
            f"Candidate registration metadata differs: {mismatches}",
        )

        database_symptoms = {
            symptom.code: symptom
            for symptom in db.scalars(select(Symptom)).all()
        }
        require(
            set(database_symptoms) == {item["code"] for item in catalogue},
            "Database symptom codes differ from the reviewed catalogue.",
        )
        for item in catalogue:
            symptom = database_symptoms[item["code"]]
            for field in ("display_name", "features", "kind", "is_red_flag"):
                require(
                    getattr(symptom, field) == item[field],
                    f"Catalogue mismatch: {item['code']}/{field}",
                )

        database_report = {
            "migration_revision": revisions[0],
            "assessment_column": "nullable JSONB",
            "active_model_version": active[0].version,
            "candidate_model_version": candidate.version,
            "candidate_active": candidate.is_active,
            "candidate_registration_matches_verified_release": True,
            "canonical_symptom_count": len(database_symptoms),
            "catalogue_fields_match": True,
            "transaction_read_only": True,
        }

        db.rollback()

    print("PASS: database schema, registry and canonical catalogue.")

    cases = verify_reference_cases(runtime, reference_document["cases"])

    # Check the bundle and wheel again after the reference calls.
    read_verified_bundle(
        candidate_loader.CANDIDATE_BUNDLE_DIRECTORY,
        runtime.provenance["manifest_sha256"],
    )
    require(
        hashlib.sha256(wheel_path.read_bytes()).hexdigest() == wheel_digest,
        "Candidate wheel changed during the audit.",
    )

    report = {
        "issue": 55,
        "status": "pre_activation_audit_passed",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database_report,
        "runtime_provenance": runtime.provenance,
        "mapping_provenance": empty_mapping["mapping_provenance"],
        "wheel_sha256": wheel_digest,
        "reference_case_count": len(cases),
        "numeric_absolute_tolerance": SCORE_TOLERANCE,
        "decisions_compared_exactly": True,
        "reference_cases": cases,
        "database_writes_performed": False,
        "activation_performed": False,
        "frontend_readiness": "not_verified",
        "rollback_rehearsal": "pending",
        "deployment_approved": False,
        "limitations": [
            "Reference cases verify software behaviour, not diagnostic accuracy.",
            "The canonical catalogue check does not audit every NLP alias.",
            "This report does not verify installed wheel module bytes.",
            "Frontend compatibility and activation approval remain separate.",
        ],
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("\nPRE-ACTIVATION AUDIT PASSED")
    print("Active model:", database_report["active_model_version"])
    print("Candidate active:", database_report["candidate_active"])
    print("Reference cases verified:", len(cases))
    print("Frontend readiness: not verified")
    print("Rollback rehearsal: pending")
    print("No database records or activation settings changed.")
    print("Saved to:", REPORT_PATH)


if __name__ == "__main__":
    main()