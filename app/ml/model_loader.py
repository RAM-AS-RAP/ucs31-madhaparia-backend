
"""Load and validate saved respiratory Random Forest models."""

import json
from functools import lru_cache
from pathlib import Path

import joblib
from app.core.errors import ServiceNotReadyError


ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"

# Existing V1 artifacts - retained for backward compatibility.
MODEL_PATH = ARTIFACT_DIR / "respiratory_random_forest_v1.joblib"
FEATURES_PATH = ARTIFACT_DIR / "model_features.json"
CLASSES_PATH = ARTIFACT_DIR / "disease_classes.json"
METADATA_PATH = ARTIFACT_DIR / "model_metadata.json"

# New V2 artifacts.
V2_MODEL_PATH = ARTIFACT_DIR / "rf_v2_23class_prototype.joblib"
V2_SCHEMA_PATH = ARTIFACT_DIR / "v2_feature_schema.json"

V2_MODEL_VERSION = "v2_23class_prototype"


def _load_json(path: Path):
    """Read a required JSON artifact."""

    if not path.exists():
        raise FileNotFoundError(
            f"Required model artifact not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_model_bundle():
    """Load the existing V1 model without changing its behaviour."""

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

    expected_feature_count = getattr(
        model, "n_features_in_", None
    )

    if (
        expected_feature_count is not None
        and feature_count != expected_feature_count
    ):
        raise ValueError(
            "Model feature count does not match model_features.json: "
            f"model expects {expected_feature_count}, "
            f"but JSON contains {feature_count}."
        )

    model_class_count = len(
        getattr(model, "classes_", [])
    )

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


def load_v2_model_bundle():
    """Load V2 separately and validate its saved feature schema.

    V2 is loaded on demand. The existing V1 prediction service
    remains unchanged until backend Issue #46.
    """

    for path in (V2_MODEL_PATH, V2_SCHEMA_PATH):
        if not path.exists():
            raise FileNotFoundError(
                f"Required V2 model artifact not found: {path}"
            )

    schema = _load_json(V2_SCHEMA_PATH)

    if not isinstance(schema, dict):
        raise ValueError("V2 feature schema must be a JSON object.")

    symptom_features = schema.get("symptom_features")
    feature_names = schema.get("feature_names")

    if not isinstance(symptom_features, list):
        raise ValueError(
            "V2 symptom_features must be a list."
        )

    if not isinstance(feature_names, list):
        raise ValueError(
            "V2 feature_names must be a list."
        )

    if (
        not all(isinstance(code, str) for code in symptom_features)
        or len(symptom_features) != 32
        or len(set(symptom_features)) != 32
    ):
        raise ValueError(
            "V2 requires exactly 32 unique symptom codes."
        )

    expected_feature_names = (
        ["AGE", "SEX_M"]
        + [
            f"{code}__present"
            for code in symptom_features
        ]
        + [
            f"{code}__absent"
            for code in symptom_features
        ]
    )

    if (
        len(feature_names) != 66
        or feature_names != expected_feature_names
    ):
        raise ValueError(
            "V2 feature schema must contain the expected "
            "66 features in the correct order."
        )

    if schema.get("feature_count") != 66:
        raise ValueError(
            "V2 schema feature_count must equal 66."
        )

    model = joblib.load(V2_MODEL_PATH)

    if getattr(model, "n_features_in_", None) != 66:
        raise ValueError(
            "Saved V2 model must expect exactly 66 features."
        )

    if hasattr(model, "feature_names_in_"):
        if list(model.feature_names_in_) != feature_names:
            raise ValueError(
                "V2 model feature order does not match its schema."
            )

    model_classes = getattr(model, "classes_", None)

    if model_classes is None or len(model_classes) != 23:
        raise ValueError(
            "Saved V2 model must contain exactly 23 classes."
        )

    disease_classes = [
        str(condition)
        for condition in model_classes
    ]

    if len(set(disease_classes)) != 23:
        raise ValueError(
            "V2 model contains duplicate class names."
        )

    if not callable(getattr(model, "predict_proba", None)):
        raise ValueError(
            "Saved V2 model must support predict_proba."
        )

    return {
        "model": model,
        "features": feature_names,
        "symptom_features": symptom_features,
        "classes": disease_classes,
        "schema": schema,
        "model_version": V2_MODEL_VERSION,
    }


@lru_cache(maxsize=1)
def _legacy_bundle():
    try:
        return load_model_bundle()
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ServiceNotReadyError('The selected legacy V1 model bundle is unavailable; no alternative was substituted.') from error


def __getattr__(name):
    # Preserve legacy imports without making static safety depend on V1 artifacts.
    keys = {'model': 'model', 'model_features': 'features', 'disease_classes': 'classes', 'model_metadata': 'metadata'}
    if name == 'model_bundle':
        return _legacy_bundle()
    if name in keys:
        return _legacy_bundle()[keys[name]]
    raise AttributeError(name)
