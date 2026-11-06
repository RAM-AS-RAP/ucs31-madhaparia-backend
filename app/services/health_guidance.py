import json
from functools import lru_cache
from pathlib import Path
from app.core.errors import ServiceNotReadyError


CONDITION_INFO_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "condition_information.json"
)


@lru_cache(maxsize=1)
def load_condition_information():
    try:
        return json.loads(CONDITION_INFO_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ServiceNotReadyError('Condition information is unavailable; static urgent guidance remains available.') from error


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
    information = load_condition_information()

    for prediction in predictions:
        condition = prediction["condition"]

        if condition not in information:
            raise ValueError(
                f"No condition information found for: {condition}"
            )

        condition_info = information[condition]

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
