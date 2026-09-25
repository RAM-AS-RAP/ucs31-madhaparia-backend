import json
from pathlib import Path

import joblib


ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"

MODEL_PATH = ARTIFACT_DIR / "respiratory_random_forest_v1.joblib"
FEATURES_PATH = ARTIFACT_DIR / "model_features.json"
CLASSES_PATH = ARTIFACT_DIR / "disease_classes.json"
METADATA_PATH = ARTIFACT_DIR / "model_metadata.json"


def _load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Required model artifact not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_model_bundle():
    required_files = [
        MODEL_PATH,
        FEATURES_PATH,
        CLASSES_PATH,
        METADATA_PATH,
    ]

    for path in required_files:
        if not path.exists():
            raise FileNotFoundError(
                f"Required model artifact not found: {path}"
            )

    model = joblib.load(MODEL_PATH)
    model_features = _load_json(FEATURES_PATH)
    disease_classes = _load_json(CLASSES_PATH)
    model_metadata = _load_json(METADATA_PATH)

    feature_count = len(model_features)
    class_count = len(disease_classes)

    expected_feature_count = getattr(model, "n_features_in_", None)

    if expected_feature_count is not None and feature_count != expected_feature_count:
        raise ValueError(
            "Model feature count does not match model_features.json: "
            f"model expects {expected_feature_count}, "
            f"but JSON contains {feature_count}."
        )

    model_class_count = len(getattr(model, "classes_", []))

    if model_class_count and class_count != model_class_count:
        raise ValueError(
            "Disease class count does not match the trained model: "
            f"model contains {model_class_count} classes, "
            f"but disease_classes.json contains {class_count}."
        )

    return {
        "model": model,
        "features": model_features,
        "classes": disease_classes,
        "metadata": model_metadata,
    }


model_bundle = load_model_bundle()

model = model_bundle["model"]
model_features = model_bundle["features"]
disease_classes = model_bundle["classes"]
model_metadata = model_bundle["metadata"]