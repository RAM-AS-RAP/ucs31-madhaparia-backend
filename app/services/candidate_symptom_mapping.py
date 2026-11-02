"""Map server-confirmed symptoms into the verified candidate schema."""

import copy
import hashlib
import json
from pathlib import Path

from app.core.errors import ServiceNotReadyError
from app.ml import candidate_loader


ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = ROOT / "app/data/candidate_symptom_mapping_v1.json"
CATALOG_PATH = ROOT / "app/nlp/canonical_symptoms.json"
MAPPING_ID = "backend_candidate_symptom_mapping_v1"


class CandidateMappingInputError(ValueError):
    """Invalid demographics or inconsistent confirmation input."""


def semantic_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require(condition, message):
    if not condition:
        raise CandidateMappingInputError(message)


def _load_mapping_context():
    try:
        from respiratory_candidate_runtime.integrity import decode_json_bytes

        runtime = candidate_loader.load_candidate_runtime()
        schema = runtime.schema

        mapping = decode_json_bytes(
            MAPPING_PATH.read_bytes(), "candidate mapping"
        )
        catalogue = decode_json_bytes(
            CATALOG_PATH.read_bytes(), "canonical catalogue"
        )

        if mapping["mapping_id"] != MAPPING_ID:
            raise ValueError("Unexpected mapping identity.")
        if mapping["schema_id"] != schema["schema_id"]:
            raise ValueError("Mapping targets another schema.")
        if mapping["schema_sha256"] != runtime.provenance["schema_sha256"]:
            raise ValueError("Mapping schema fingerprint mismatch.")
        if semantic_sha256(catalogue) != mapping["canonical_catalog_semantic_sha256"]:
            raise ValueError("Canonical catalogue contents changed.")

        by_code = {item["code"]: item for item in catalogue}
        if len(by_code) != len(catalogue):
            raise ValueError("Duplicate canonical symptom codes.")

        supported = set(mapping["supported"])
        clarification = set(mapping["requires_clarification"])
        safety = set(mapping["safety_only"])

        if (
            supported & clarification
            or supported & safety
            or clarification & safety
        ):
            raise ValueError("Mapping categories overlap.")

        if supported | clarification | safety != set(by_code):
            raise ValueError("Mapping does not cover the canonical catalogue.")

        concepts = set(schema["symptom_features"])
        covered_concepts = set()

        for code, mapped in mapping["supported"].items():
            if (
                not isinstance(mapped, list)
                or not mapped
                or any(not isinstance(item, str) for item in mapped)
                or len(mapped) != len(set(mapped))
                or not set(mapped) <= concepts
            ):
                raise ValueError(f"Invalid supported mapping: {code}")

            if code == "headache":
                expected = {"E_53", "head_region_pain"}
            elif code == "chest_pain":
                expected = {"E_53", "chest_region_pain"}
            else:
                expected = set(by_code[code]["features"])

            if set(mapped) != expected:
                raise ValueError(f"Unexpected concept mapping: {code}")

            covered_concepts.update(mapped)

        for code, definition in mapping["requires_clarification"].items():
            targets = definition["unasserted_concepts"]
            meaning = definition["required_meaning"]
            if (
                not isinstance(targets, list)
                or not targets
                or len(targets) != len(set(targets))
                or not set(targets) <= concepts
                or set(targets) != set(by_code[code]["features"])
                or not isinstance(meaning, str)
                or not meaning.strip()
            ):
                raise ValueError(f"Invalid clarification definition: {code}")
            covered_concepts.update(targets)

        if covered_concepts != concepts:
            raise ValueError("Mapping concept coverage differs from the schema.")

        expected_flags = {
            code
            for code, item in by_code.items()
            if item["is_red_flag"] is True
        }
        if set(mapping["expected_red_flag_codes"]) != expected_flags:
            raise ValueError("Reviewed safety flags differ from the catalogue.")

        for code in safety:
            if by_code[code]["features"] or code not in expected_flags:
                raise ValueError(f"Invalid safety-only definition: {code}")

        provenance = {
            "mapping_id": MAPPING_ID,
            "mapping_sha256": semantic_sha256(mapping),
            "canonical_catalog_semantic_sha256": semantic_sha256(catalogue),
            "json_fingerprint_encoding": (
                "UTF-8 JSON; sorted keys; compact separators; ensure_ascii=False"
            ),
            "schema_id": schema["schema_id"],
            "schema_sha256": runtime.provenance["schema_sha256"],
            "bundle_manifest_sha256": runtime.provenance["manifest_sha256"],
        }
        return mapping, by_code, schema, provenance

    except ServiceNotReadyError:
        raise
    except Exception as error:
        raise ServiceNotReadyError(
            "The reviewed candidate symptom mapping is unavailable."
        ) from error


def _index_symptoms(items, catalogue):
    _require(isinstance(items, list), "Symptom records must be a list.")
    result = {}

    for item in items:
        _require(isinstance(item, dict), "Invalid symptom record.")
        code = item.get("code")
        _require(
            isinstance(code, str) and code in catalogue,
            "Symptom code is outside the reviewed catalogue.",
        )
        _require(
            type(item.get("confirmed")) is bool,
            "Each symptom requires a boolean confirmation state.",
        )

        expected = catalogue[code]
        for field in ("display_name", "features", "kind", "is_red_flag"):
            if (
                type(item.get(field)) is not type(expected[field])
                or item[field] != expected[field]
            ):
                raise ServiceNotReadyError(
                    f"Confirmed catalogue metadata changed: {code}/{field}"
                )

        if code in result:
            _require(
                result[code]["confirmed"] == item["confirmed"],
                f"Conflicting confirmation states: {code}",
            )
        result[code] = item

    return result


def map_candidate_confirmation(age, sex, confirmation):
    """Map a result produced by the backend's confirm_symptoms service.

    This function is not an HTTP request validator. Callers must obtain
    confirmation from the server service rather than accept a client-
    supplied confirmation dictionary.
    """

    _require(
        type(age) is int and 0 <= age <= 120,
        "Age must be an integer from 0 through 120.",
    )
    _require(
        type(sex) is str and sex in {"M", "F"},
        "Sex must be M or F.",
    )
    _require(isinstance(confirmation, dict), "Invalid confirmation result.")
    _require(
        confirmation.get("requires_confirmation") is False,
        "A completed server confirmation is required.",
    )

    codes = confirmation.get("confirmed_codes")
    _require(
        isinstance(codes, list)
        and len(codes) <= 50
        and all(isinstance(code, str) and code for code in codes),
        "Invalid confirmed-code list.",
    )

    unmatched = confirmation.get("unmatched_wording")
    _require(
        isinstance(unmatched, list),
        "Unmatched wording must be preserved as a list.",
    )

    mapping, catalogue, schema, provenance = _load_mapping_context()

    recognised = _index_symptoms(
        confirmation.get("recognised_symptoms"), catalogue
    )
    confirmed = _index_symptoms(
        confirmation.get("confirmed_symptoms"), catalogue
    )

    selected = set(codes)
    marked_confirmed = {
        code for code, item in recognised.items() if item["confirmed"]
    }

    _require(
        selected <= set(recognised)
        and selected == set(confirmed) == marked_confirmed
        and all(item["confirmed"] for item in confirmed.values()),
        "Confirmed codes and recognised symptom records disagree.",
    )

    safety_flags = sorted(
        code for code in selected if catalogue[code]["is_red_flag"]
    )
    _require(
        type(confirmation.get("has_red_flag")) is bool
        and confirmation["has_red_flag"] == bool(safety_flags),
        "Confirmation safety flags disagree.",
    )

    supported_codes = sorted(selected & set(mapping["supported"]))
    clarification_codes = sorted(
        selected & set(mapping["requires_clarification"])
    )
    safety_only_codes = sorted(selected & set(mapping["safety_only"]))

    present = {
        concept
        for code in supported_codes
        for concept in mapping["supported"][code]
    }

    state = {
        "present": [
            code for code in schema["symptom_features"] if code in present
        ],
        "absent": [],
        "unknown": [
            code for code in schema["symptom_features"] if code not in present
        ],
    }

    from respiratory_candidate_runtime.features import (
        get_reporting_units,
        validate_candidate_features,
    )

    features = {name: 0.0 for name in schema["feature_names"]}
    features["AGE"] = float(age)
    features["SEX_M"] = float(sex == "M")

    for concept in present:
        features[f"{concept}__present"] = 1.0

    units = get_reporting_units(state, schema)
    validated, unit_count, absent = validate_candidate_features(
        features, schema
    )

    if unit_count != len(units) or absent:
        raise ServiceNotReadyError("Candidate feature construction failed.")

    return {
        "feature_values": validated,
        "confirmed_codes": sorted(selected),
        "supported_codes": supported_codes,
        "unsupported_symptoms": sorted(
            set(clarification_codes) | set(safety_only_codes)
        ),
        "clarification_required": [
            {
                "code": code,
                **copy.deepcopy(mapping["requires_clarification"][code]),
            }
            for code in clarification_codes
        ],
        "safety_flags": safety_flags,
        "has_red_flag": bool(safety_flags),
        "symptom_state": state,
        "reporting_units": units,
        "reported_unit_count": unit_count,
        "unmatched_wording": copy.deepcopy(unmatched),
        "has_unmatched_wording": bool(unmatched),
        "mapping_provenance": provenance,
    }