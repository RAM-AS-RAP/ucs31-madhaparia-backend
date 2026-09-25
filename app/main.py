from fastapi import FastAPI
from pydantic import BaseModel

from app.core.config import settings
from app.ml.model_loader import model, model_features, disease_classes
from app.nlp.symptom_mapper import build_confirmation_output


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Backend API for the Respiratory Health Awareness "
        "and Prediction System."
    ),
)


class SymptomTextRequest(BaseModel):
    text: str


@app.get("/")
def root():
    return {
        "message": "Respiratory Health Awareness API is running."
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }


@app.get("/model/status")
def model_status():
    return {
        "status": "loaded",
        "model_type": type(model).__name__,
        "feature_count": len(model_features),
        "model_feature_count": int(model.n_features_in_),
        "class_count": len(disease_classes),
        "model_class_count": len(model.classes_),
    }


@app.post("/nlp/map")
def map_symptoms(request: SymptomTextRequest):
    return build_confirmation_output(request.text)