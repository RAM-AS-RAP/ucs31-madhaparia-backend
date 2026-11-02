"""Test candidate mapping through the database-backed confirmation service."""

import copy
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.errors import ServiceNotReadyError
from app.db.models import Symptom
from app.ml.candidate_loader import load_candidate_runtime
from app.services.candidate_symptom_mapping import map_candidate_confirmation
from app.services.symptom_confirmation import confirm_symptoms


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


@pytest.fixture(autouse=True)
def forbid_predictions(monkeypatch):
    runtime = load_candidate_runtime()
    predict = Mock(
        side_effect=AssertionError("Mapping must not calculate predictions.")
    )
    monkeypatch.setattr(runtime, "predict", predict)
    yield
    predict.assert_not_called()


def confirm_and_map(db, text, codes):
    confirmed = confirm_symptoms(
        text=text,
        confirmed_codes=codes,
        db=db,
    )
    original = copy.deepcopy(confirmed)
    mapped = map_candidate_confirmation(22, "F", confirmed)
    assert confirmed == original
    return confirmed, mapped


@pytest.mark.parametrize(
    "text",
    [
        "I have fever and cough",
        "I have fever na kikohozi",
        "I have feaver and couhg",
    ],
)
def test_equivalent_language_and_spelling_inputs(db, text):
    _, mapped = confirm_and_map(db, text, ["fever", "cough"])

    assert set(mapped["symptom_state"]["present"]) == {"E_91", "E_201"}
    assert mapped["reported_unit_count"] == 2
    assert mapped["unsupported_symptoms"] == []
    assert mapped["safety_flags"] == []
    assert mapped["feature_values"]["AGE"] == 22.0
    assert mapped["feature_values"]["SEX_M"] == 0.0


def test_confirmed_headache_and_chest_pain_keep_distinct_regions(db):
    head_confirmation, head = confirm_and_map(
        db,
        "I have tiredness and headache",
        ["tiredness", "headache"],
    )
    chest_confirmation, chest = confirm_and_map(
        db,
        "I have tiredness and chest pain",
        ["tiredness", "chest_pain"],
    )

    assert head["feature_values"] != chest["feature_values"]
    assert set(head["symptom_state"]["present"]) == {
        "E_89", "E_53", "head_region_pain"
    }
    assert set(chest["symptom_state"]["present"]) == {
        "E_89", "E_53", "chest_region_pain"
    }
    assert head["reported_unit_count"] == chest["reported_unit_count"] == 2

    # The existing confirmation output retains its legacy mapping.
    assert "E_55_@_V_89" in head_confirmation["model_features"]
    assert "E_55_@_V_101" in chest_confirmation["model_features"]

    # Candidate features contain region concepts, not exact locations.
    assert not any("_@_" in name for name in head["feature_values"])
    assert not any("_@_" in name for name in chest["feature_values"])


def test_legacy_flat_feature_list_does_not_control_candidate_mapping(db):
    confirmed, original = confirm_and_map(
        db, "I have fever and cough", ["fever", "cough"]
    )

    confirmed["model_features"] = ["E_162"]
    repeated = map_candidate_confirmation(22, "F", confirmed)

    assert repeated == original
    assert repeated["feature_values"]["E_162__present"] == 0


def test_rejected_cough_is_unknown_not_absent(db):
    confirmed, mapped = confirm_and_map(
        db, "I have fever and cough", ["fever"]
    )

    assert any(
        item["code"] == "cough"
        for item in confirmed["rejected_symptoms"]
    )
    assert mapped["feature_values"]["E_201__present"] == 0
    assert mapped["feature_values"]["E_201__absent"] == 0
    assert mapped["reported_unit_count"] == 1


def test_no_confirmed_symptoms_produces_all_unknown_concepts(db):
    _, mapped = confirm_and_map(db, "I have fever and cough", [])

    assert mapped["confirmed_codes"] == []
    assert mapped["reported_unit_count"] == 0
    assert mapped["symptom_state"]["present"] == []
    assert mapped["symptom_state"]["absent"] == []
    assert len(mapped["symptom_state"]["unknown"]) == 34


@pytest.mark.parametrize(
    "text, code",
    [
        ("I am coughing up blood", "coughing_blood"),
        ("I have shortness of breath", "shortness_of_breath"),
        ("I have stridor", "stridor"),
        ("I have drooling", "drooling"),
        ("I have chest pain at rest", "chest_pain_at_rest"),
        ("My lips are turning blue", "blue_lips"),
        ("I have confusion", "confusion"),
        ("I have fainting", "fainting"),
    ],
)
def test_confirmed_safety_flags_survive_candidate_mapping(db, text, code):
    confirmed, mapped = confirm_and_map(db, text, [code])

    assert confirmed["has_red_flag"] is True
    assert confirmed["safety_status"] == "urgent_review"
    assert mapped["has_red_flag"] is True
    assert mapped["safety_flags"] == [code]

    if code in {"blue_lips", "confusion", "fainting", "drooling"}:
        assert mapped["unsupported_symptoms"] == [code]
        assert mapped["reported_unit_count"] == 0


def test_sneezing_remains_unmatched_without_invented_features(db):
    confirmed, mapped = confirm_and_map(
        db, "I have fever and sneezing", ["fever"]
    )

    recognised_codes = {
        item["code"] for item in confirmed["recognised_symptoms"]
    }
    assert "sneezing" not in recognised_codes
    assert "wheezing" not in recognised_codes
    assert confirmed["unmatched_wording"]
    assert mapped["unmatched_wording"] == confirmed["unmatched_wording"]
    assert mapped["has_unmatched_wording"] is True
    assert mapped["symptom_state"]["present"] == ["E_91"]


@pytest.mark.parametrize(
    "text, code, concept",
    [
        ("I have itchy eyes", "itchy_eyes", "E_170"),
        ("I have muscle pain", "muscle_aches", "E_144"),
    ],
)
def test_broad_confirmed_labels_preserve_clarification_requirement(
    db, text, code, concept
):
    _, mapped = confirm_and_map(db, text, [code])

    assert mapped["unsupported_symptoms"] == [code]
    assert mapped["clarification_required"][0]["code"] == code
    assert mapped["clarification_required"][0]["required_meaning"]
    assert mapped["feature_values"][f"{concept}__present"] == 0
    assert mapped["feature_values"][f"{concept}__absent"] == 0


def test_live_catalogue_label_change_is_detected(db):
    symptom = db.scalar(select(Symptom).where(Symptom.code == "tiredness"))
    assert symptom is not None

    symptom.display_name = "Any tiredness"
    db.flush()

    confirmed = confirm_symptoms(
        text="I have tiredness",
        confirmed_codes=["tiredness"],
        db=db,
    )

    with pytest.raises(ServiceNotReadyError, match="metadata changed"):
        map_candidate_confirmation(22, "F", confirmed)

    # The fixture rolls back this temporary test-schema change.


def test_confirmation_rejects_a_code_not_recognised_from_text(db):
    with pytest.raises(ValueError, match="not recognised"):
        confirm_symptoms(
            text="I have fever",
            confirmed_codes=["chest_pain"],
            db=db,
        )