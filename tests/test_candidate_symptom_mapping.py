"""Standalone tests for candidate confirmation-to-feature mapping."""

import copy
import json
from types import SimpleNamespace

import pytest

from app.core.errors import ServiceNotReadyError
from app.services import candidate_symptom_mapping as mapper


@pytest.fixture
def context(monkeypatch):
    catalogue = json.loads(mapper.CATALOG_PATH.read_text(encoding="utf-8"))
    schema = json.loads(
        (
            mapper.candidate_loader.CANDIDATE_BUNDLE_DIRECTORY
            / "feature_schema.json"
        ).read_text(encoding="utf-8")
    )
    release = json.loads(
        mapper.candidate_loader.CANDIDATE_RELEASE_RECORD.read_text(
            encoding="utf-8"
        )
    )

    runtime = SimpleNamespace(
        schema=schema,
        provenance={
            "schema_sha256": (
                "1b8164ea98967070af7250c72985fb5cb5c97dfe63e80f29e13fd0fcca012c66"
            ),
            "manifest_sha256": release["bundle_manifest_sha256"],
        },
    )
    monkeypatch.setattr(
        mapper.candidate_loader, "load_candidate_runtime", lambda: runtime
    )
    return {item["code"]: item for item in catalogue}, schema


def confirmation(catalogue, codes, rejected=()):
    recognised = []

    for code in dict.fromkeys([*codes, *rejected]):
        item = copy.deepcopy(catalogue[code])
        item["confirmed"] = code in codes
        recognised.append(item)

    return {
        "requires_confirmation": False,
        "confirmed_codes": list(codes),
        "recognised_symptoms": recognised,
        "confirmed_symptoms": [
            copy.deepcopy(item) for item in recognised if item["confirmed"]
        ],
        "rejected_symptoms": [
            copy.deepcopy(item) for item in recognised if not item["confirmed"]
        ],
        "has_red_flag": any(
            item["is_red_flag"] for item in recognised if item["confirmed"]
        ),
        "unmatched_wording": [],
        "model_features": ["unused_legacy_feature"],
    }


def test_pain_regions_remain_distinct(context):
    catalogue, schema = context
    head = mapper.map_candidate_confirmation(
        22, "F", confirmation(catalogue, ["tiredness", "headache"])
    )
    chest = mapper.map_candidate_confirmation(
        22, "F", confirmation(catalogue, ["tiredness", "chest_pain"])
    )

    assert head["feature_values"] != chest["feature_values"]
    assert head["feature_values"]["head_region_pain__present"] == 1
    assert head["feature_values"]["chest_region_pain__present"] == 0
    assert chest["feature_values"]["chest_region_pain__present"] == 1
    assert chest["feature_values"]["head_region_pain__present"] == 0

    for result in (head, chest):
        assert list(result["feature_values"]) == schema["feature_names"]
        assert len(result["feature_values"]) == 70
        assert result["feature_values"]["E_53__present"] == 1
        assert result["reported_unit_count"] == 2
        assert "E_53" not in result["reporting_units"]
        assert result["symptom_state"]["absent"] == []
        assert all(
            value == 0
            for name, value in result["feature_values"].items()
            if name.endswith("__absent")
        )


def test_rejected_suggestions_remain_unknown(context):
    catalogue, _ = context
    result = mapper.map_candidate_confirmation(
        22, "F", confirmation(catalogue, ["fever"], rejected=["cough"])
    )
    assert result["feature_values"]["E_91__present"] == 1
    assert result["feature_values"]["E_201__present"] == 0
    assert result["feature_values"]["E_201__absent"] == 0
    assert "E_201" in result["symptom_state"]["unknown"]


def test_duplicates_do_not_add_reporting_units(context):
    catalogue, _ = context
    original = confirmation(catalogue, ["fever", "cough"])
    repeated = confirmation(catalogue, ["fever", "fever", "cough"])
    repeated["confirmed_symptoms"].append(
        copy.deepcopy(repeated["confirmed_symptoms"][0])
    )

    first = mapper.map_candidate_confirmation(22, "F", original)
    second = mapper.map_candidate_confirmation(22, "F", repeated)
    assert first == second
    assert first["reported_unit_count"] == 2


@pytest.mark.parametrize(
    "code",
    [
        "drooling", "hoarse_voice", "runny_blocked_nose", "itchy_eyes",
        "pain_deep_breath", "muscle_aches", "heavy_sweating",
        "loss_of_appetite", "weight_loss",
    ],
)
def test_missing_qualifiers_do_not_become_numerical_evidence(context, code):
    catalogue, _ = context
    result = mapper.map_candidate_confirmation(
        22, "F", confirmation(catalogue, [code])
    )

    assert result["reported_unit_count"] == 0
    assert result["unsupported_symptoms"] == [code]
    assert result["clarification_required"][0]["code"] == code
    assert result["symptom_state"]["present"] == []
    assert result["safety_flags"] == (
        [code] if catalogue[code]["is_red_flag"] else []
    )


@pytest.mark.parametrize(
    "code",
    [
        "coughing_blood", "shortness_of_breath", "stridor", "drooling",
        "chest_pain_at_rest", "blue_lips", "confusion", "fainting",
    ],
)
def test_all_existing_confirmed_red_flags_are_preserved(context, code):
    catalogue, _ = context
    result = mapper.map_candidate_confirmation(
        22, "F", confirmation(catalogue, [code])
    )
    assert result["has_red_flag"] is True
    assert result["safety_flags"] == [code]


def test_empty_confirmation_and_unmatched_wording(context):
    catalogue, schema = context
    supplied = confirmation(catalogue, [])
    supplied["unmatched_wording"] = [
        {"unmatched_text": "sneezing", "suggestions": []}
    ]
    original = copy.deepcopy(supplied)

    result = mapper.map_candidate_confirmation(22, "F", supplied)

    assert result["reported_unit_count"] == 0
    assert result["symptom_state"]["unknown"] == schema["symptom_features"]
    assert result["unmatched_wording"] == supplied["unmatched_wording"]
    assert result["has_unmatched_wording"] is True
    assert supplied == original

    result["unmatched_wording"][0]["unmatched_text"] = "changed"
    assert supplied == original


@pytest.mark.parametrize("age", [True, 22.5, "22", -1, 121, None])
def test_invalid_demographics_are_rejected(context, age):
    catalogue, _ = context
    with pytest.raises(mapper.CandidateMappingInputError):
        mapper.map_candidate_confirmation(
            age, "F", confirmation(catalogue, ["fever"])
        )


@pytest.mark.parametrize("sex", ["female", "f", "", None, True])
def test_invalid_sex_is_rejected(context, sex):
    catalogue, _ = context
    with pytest.raises(mapper.CandidateMappingInputError):
        mapper.map_candidate_confirmation(
            22, sex, confirmation(catalogue, ["fever"])
        )


def test_unrecognised_confirmed_code_is_rejected(context):
    catalogue, _ = context
    supplied = confirmation(catalogue, ["fever"])
    supplied["confirmed_codes"].append("sneezing")

    with pytest.raises(mapper.CandidateMappingInputError):
        mapper.map_candidate_confirmation(22, "F", supplied)


def test_changed_confirmation_label_is_not_silently_accepted(context):
    catalogue, _ = context
    supplied = confirmation(catalogue, ["tiredness"])
    supplied["confirmed_symptoms"][0]["display_name"] = "Any tiredness"

    with pytest.raises(ServiceNotReadyError, match="metadata changed"):
        mapper.map_candidate_confirmation(22, "F", supplied)


def test_inconsistent_safety_flag_is_rejected(context):
    catalogue, _ = context
    supplied = confirmation(catalogue, ["blue_lips"])
    supplied["has_red_flag"] = False

    with pytest.raises(mapper.CandidateMappingInputError):
        mapper.map_candidate_confirmation(22, "F", supplied)


def test_changed_catalogue_is_rejected(context, tmp_path, monkeypatch):
    changed = json.loads(mapper.CATALOG_PATH.read_text(encoding="utf-8"))
    changed[0]["display_name"] = "Changed meaning"
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(mapper, "CATALOG_PATH", path)

    with pytest.raises(ServiceNotReadyError):
        mapper.map_candidate_confirmation(
            22, "F", confirmation(context[0], ["fever"])
        )


def test_catalogue_formatting_does_not_change_semantic_fingerprint(context):
    catalogue = json.loads(mapper.CATALOG_PATH.read_text(encoding="utf-8"))
    reformatted = json.loads(json.dumps(catalogue, indent=4, sort_keys=True))
    assert mapper.semantic_sha256(catalogue) == mapper.semantic_sha256(reformatted)