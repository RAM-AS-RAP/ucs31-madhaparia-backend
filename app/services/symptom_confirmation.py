from app.nlp.symptom_mapper import build_confirmation_output


def confirm_symptoms(text: str, confirmed_codes: list[str]):
    mapping_result = build_confirmation_output(text)

    recognised_symptoms = mapping_result["recognised_symptoms"]

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

        is_confirmed = symptom["code"] in confirmed_code_set
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

    has_red_flag = len(red_flag_symptoms) > 0

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
    }