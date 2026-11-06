from app.nlp.symptom_mapper import build_confirmation_output
from sqlalchemy.orm import Session
from copy import deepcopy

def confirm_symptoms(
    text: str,
    confirmed_codes: list[str],
    db: Session,
    corrections: list[dict] | None = None,
):
    mapping_result = build_confirmation_output(
        text,
        db,
    )
    return confirm_mapping(mapping_result, confirmed_codes, corrections)


def confirm_mapping(mapping_result, confirmed_codes, corrections=None):
    """Shared server confirmation for API and reproducible service-level replay."""
    text = mapping_result['input_text']

    recognised_symptoms = deepcopy(mapping_result["recognised_symptoms"])
    for symptom in recognised_symptoms:
        symptom['original_status'] = symptom.get('status')
        symptom['original_subject'] = symptom.get('reported_subject')
        symptom['corrected_by_patient'] = False
    corrections = corrections or []
    seen = set()
    for correction in corrections:
        code = correction.get('code')
        symptom = next((s for s in recognised_symptoms if s['code'] == code), None)
        if symptom is None or code in seen or correction.get('status') not in {'present', 'absent', 'unknown'} or correction.get('subject') not in {'patient_or_unspecified', 'other_person'}:
            raise ValueError('Invalid or duplicate symptom correction.')
        seen.add(code)
        symptom.update(status=correction['status'], reported_subject=correction['subject'], corrected_by_patient=True)

    recognised_by_code = {
        symptom["code"]: symptom
        for symptom in recognised_symptoms
    }

    invalid_codes = [
        code
        for code in confirmed_codes
        if code not in recognised_by_code
    ]

    if invalid_codes:
        raise ValueError(
            "The following symptom codes were not recognised "
            f"from the submitted text: {invalid_codes}"
        )

    confirmed_code_set = set(confirmed_codes)

    updated_symptoms = []
    confirmed_symptoms = []
    rejected_symptoms = []
    model_features = []
    red_flag_symptoms = []

    for symptom in recognised_symptoms:
        symptom_result = dict(symptom)

        is_confirmed = (symptom["code"] in confirmed_code_set
                        and symptom.get('status', 'present') == 'present'
                        and symptom.get('reported_subject') != 'other_person')
        symptom_result["confirmed"] = is_confirmed

        updated_symptoms.append(symptom_result)

        if is_confirmed:
            confirmed_symptoms.append(symptom_result)

            for feature in symptom.get("features", []):
                if feature not in model_features:
                    model_features.append(feature)

            if symptom.get("is_red_flag", False):
                red_flag_symptoms.append(symptom_result)

        else:
            rejected_symptoms.append(symptom_result)

    # Safety is independent of ordinary disease-feature checkbox acceptance.
    red_flag_symptoms = [s for s in recognised_symptoms if s['is_red_flag']
                        and s.get('status') != 'absent' and s.get('reported_subject') != 'other_person']
    has_red_flag = bool(red_flag_symptoms)

    safety_status = (
        "urgent_review"
        if has_red_flag
        else "continue"
    )

    return {
        "input_text": text,
        "recognised_symptoms": updated_symptoms,
        "confirmed_symptoms": confirmed_symptoms,
        "rejected_symptoms": rejected_symptoms,
        "confirmed_codes": [
            symptom["code"]
            for symptom in confirmed_symptoms
        ],
        "model_features": model_features,
        "unmatched_wording": mapping_result["unmatched_wording"],
        "has_red_flag": has_red_flag,
        "red_flag_symptoms": red_flag_symptoms,
        "safety_status": safety_status,
        "requires_confirmation": False,
        "corrections": corrections,
        "mapping_version": mapping_result.get('mapping_version'),
        'mapping_provenance': mapping_result.get('mapping_provenance'),
    }
