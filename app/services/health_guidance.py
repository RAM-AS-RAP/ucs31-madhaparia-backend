import json
from pathlib import Path


CONDITION_INFO_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "condition_information.json"
)


with CONDITION_INFO_PATH.open(
    "r",
    encoding="utf-8",
) as file:
    CONDITION_INFORMATION = json.load(file)


CONFIDENCE_EXPLANATION = (
    "The model confidence shows how strongly the submitted "
    "symptom pattern matched each condition in the trained model. "
    "It is not the probability that you have that condition and "
    "it is not a medical diagnosis."
)


GENERAL_GUIDANCE = (
    "Use these results as general health-awareness information. "
    "If symptoms persist, worsen, become severe, or concern you, "
    "consider seeking assessment from a healthcare professional."
)


URGENT_GUIDANCE = (
    "A confirmed red-flag symptom was detected. "
    "Seek urgent medical assessment. If symptoms are severe "
    "or rapidly worsening, contact local emergency services."
)


DISCLAIMER = (
    "This system provides health-awareness information only. "
    "It does not provide a medical diagnosis and should not "
    "replace assessment by a qualified healthcare professional."
)


def enrich_predictions(
    predictions: list[dict],
) -> list[dict]:
    enriched_predictions = []

    for prediction in predictions:
        condition = prediction["condition"]

        if condition not in CONDITION_INFORMATION:
            raise ValueError(
                f"No condition information found for: {condition}"
            )

        condition_info = CONDITION_INFORMATION[condition]

        enriched_predictions.append(
            {
                "rank": prediction["rank"],
                "condition": condition,
                "display_name": condition_info["display_name"],
                "model_confidence_percent": (
                    prediction["confidence_percent"]
                ),
                "description": (
                    condition_info["short_description"]
                ),
            }
        )

    return enriched_predictions


def get_prediction_guidance() -> dict:
    return {
        "confidence_explanation": CONFIDENCE_EXPLANATION,
        "health_guidance": GENERAL_GUIDANCE,
        "disclaimer": DISCLAIMER,
    }


def get_urgent_guidance() -> dict:
    return {
        "health_guidance": URGENT_GUIDANCE,
        "disclaimer": DISCLAIMER,
    }