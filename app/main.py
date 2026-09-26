from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sqlalchemy import text

from app.core.database import engine
from app.core.config import settings
from app.ml.model_loader import model, model_features, disease_classes
from app.nlp.symptom_mapper import build_confirmation_output
from app.services.prediction import predict_top_conditions
from app.services.symptom_confirmation import confirm_symptoms

from app.services.health_guidance import (
    enrich_predictions,
    get_prediction_guidance,
    get_urgent_guidance,
)

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


class SymptomConfirmationRequest(BaseModel):
    text: str
    confirmed_codes: list[str]


class PredictionRequest(BaseModel):
    age: int = Field(ge=0, le=120)
    sex: Literal["M", "F"]
    text: str
    confirmed_codes: list[str]


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

@app.get("/database/status")
def database_status():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "connected",
            "database": "postgresql",
        }

    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Database connection unavailable.",
        ) from error


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


@app.post("/symptoms/confirm")
def confirm_recognised_symptoms(
    request: SymptomConfirmationRequest,
):
    try:
        return confirm_symptoms(
            text=request.text,
            confirmed_codes=request.confirmed_codes,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


@app.post("/predict")
def predict_conditions(request: PredictionRequest):
    try:
        confirmation = confirm_symptoms(
            text=request.text,
            confirmed_codes=request.confirmed_codes,
        )

        if confirmation["has_red_flag"]:
            urgent_guidance = get_urgent_guidance()

            return {
                "status": "urgent_review",
                "age": request.age,
                "sex": request.sex,
                "confirmed_codes": confirmation["confirmed_codes"],
                "model_features": confirmation["model_features"],
                "has_red_flag": True,
                "red_flag_symptoms": confirmation["red_flag_symptoms"],
                "predictions": [],
                **urgent_guidance,
            }

        prediction_result = predict_top_conditions(
            age=request.age,
            sex=request.sex,
            symptom_features=confirmation["model_features"],
        )

        enriched_predictions = enrich_predictions(
            prediction_result["predictions"]
        )

        guidance = get_prediction_guidance()

        return {
            "status": "prediction_complete",
            "age": request.age,
            "sex": request.sex,
            "confirmed_codes": confirmation["confirmed_codes"],
            "model_features": confirmation["model_features"],
            "has_red_flag": False,
            "feature_count": prediction_result["feature_count"],
            "predictions": enriched_predictions,
            **guidance,
        }

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error