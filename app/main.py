from typing import Literal
import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine, get_db
from app.ml.model_loader import (
    model,
    model_features,
    disease_classes,
)
from app.nlp.symptom_mapper import build_confirmation_output
from app.services.health_guidance import (
    enrich_predictions,
    get_prediction_guidance,
    get_urgent_guidance,
)
from app.services.history import (
    get_prediction_session,
    list_prediction_sessions,
    save_feedback,
    save_prediction_session,
)
from app.services.prediction import predict_top_conditions
from app.services.symptom_confirmation import confirm_symptoms


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Backend API for the Respiratory Health Awareness "
        "and Prediction System."
    ),
)


# ---------------------------------------------------------
# Request Models
# ---------------------------------------------------------

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


class FeedbackRequest(BaseModel):
    prediction_id: uuid.UUID
    was_helpful: bool
    comment: str | None = Field(
        default=None,
        max_length=1000,
    )


# ---------------------------------------------------------
# General Endpoints
# ---------------------------------------------------------

@app.get("/")
def root():
    return {
        "message": (
            "Respiratory Health Awareness API is running."
        )
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
        "model_feature_count": int(
            model.n_features_in_
        ),
        "class_count": len(disease_classes),
        "model_class_count": len(
            model.classes_
        ),
    }


# ---------------------------------------------------------
# NLP Endpoints
# ---------------------------------------------------------

@app.post("/nlp/map")
def map_symptoms(
    request: SymptomTextRequest,
):
    return build_confirmation_output(
        request.text
    )


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


# ---------------------------------------------------------
# Prediction Endpoint
# ---------------------------------------------------------

@app.post("/predict")
def predict_conditions(
    request: PredictionRequest,
    db: Session = Depends(get_db),
):
    try:
        confirmation = confirm_symptoms(
            text=request.text,
            confirmed_codes=request.confirmed_codes,
        )

        # -------------------------------------------------
        # Red-flag route
        # -------------------------------------------------

        if confirmation["has_red_flag"]:
            urgent_guidance = (
                get_urgent_guidance()
            )

            prediction_session = (
                save_prediction_session(
                    db,
                    age=request.age,
                    sex=request.sex,
                    input_text=request.text,
                    confirmed_codes=(
                        confirmation[
                            "confirmed_codes"
                        ]
                    ),
                    model_features=(
                        confirmation[
                            "model_features"
                        ]
                    ),
                    status="urgent_review",
                    has_red_flag=True,
                    predictions=[],
                )
            )

            return {
                "prediction_id": str(
                    prediction_session.id
                ),
                "status": "urgent_review",
                "age": request.age,
                "sex": request.sex,
                "confirmed_codes": (
                    confirmation[
                        "confirmed_codes"
                    ]
                ),
                "model_features": (
                    confirmation[
                        "model_features"
                    ]
                ),
                "has_red_flag": True,
                "red_flag_symptoms": (
                    confirmation[
                        "red_flag_symptoms"
                    ]
                ),
                "predictions": [],
                **urgent_guidance,
            }

        # -------------------------------------------------
        # Normal prediction route
        # -------------------------------------------------

        prediction_result = (
            predict_top_conditions(
                age=request.age,
                sex=request.sex,
                symptom_features=(
                    confirmation[
                        "model_features"
                    ]
                ),
            )
        )

        enriched_predictions = (
            enrich_predictions(
                prediction_result[
                    "predictions"
                ]
            )
        )

        guidance = (
            get_prediction_guidance()
        )

        prediction_session = (
            save_prediction_session(
                db,
                age=request.age,
                sex=request.sex,
                input_text=request.text,
                confirmed_codes=(
                    confirmation[
                        "confirmed_codes"
                    ]
                ),
                model_features=(
                    confirmation[
                        "model_features"
                    ]
                ),
                status="prediction_complete",
                has_red_flag=False,
                predictions=(
                    enriched_predictions
                ),
            )
        )

        return {
            "prediction_id": str(
                prediction_session.id
            ),
            "status": "prediction_complete",
            "age": request.age,
            "sex": request.sex,
            "confirmed_codes": (
                confirmation[
                    "confirmed_codes"
                ]
            ),
            "model_features": (
                confirmation[
                    "model_features"
                ]
            ),
            "has_red_flag": False,
            "feature_count": (
                prediction_result[
                    "feature_count"
                ]
            ),
            "predictions": (
                enriched_predictions
            ),
            **guidance,
        }

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


# ---------------------------------------------------------
# Prediction History Endpoints
# ---------------------------------------------------------

@app.get("/history")
def prediction_history(
    limit: int = 20,
    db: Session = Depends(get_db),
):
    limit = max(
        1,
        min(limit, 100),
    )

    sessions = list_prediction_sessions(
        db,
        limit=limit,
    )

    return {
        "count": len(sessions),
        "history": [
            {
                "prediction_id": str(
                    session.id
                ),
                "age": session.age,
                "sex": session.sex,
                "input_text": (
                    session.input_text
                ),
                "confirmed_codes": (
                    session.confirmed_codes
                ),
                "status": session.status,
                "has_red_flag": (
                    session.has_red_flag
                ),
                "predictions": (
                    session.predictions
                ),
                "created_at": (
                    session.created_at
                ),
            }
            for session in sessions
        ],
    }


@app.get(
    "/history/{prediction_id}"
)
def prediction_history_item(
    prediction_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    session = get_prediction_session(
        db,
        prediction_id,
    )

    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Prediction session not found."
            ),
        )

    return {
        "prediction_id": str(
            session.id
        ),
        "age": session.age,
        "sex": session.sex,
        "input_text": session.input_text,
        "confirmed_codes": (
            session.confirmed_codes
        ),
        "model_features": (
            session.model_features
        ),
        "status": session.status,
        "has_red_flag": (
            session.has_red_flag
        ),
        "predictions": (
            session.predictions
        ),
        "created_at": (
            session.created_at
        ),
    }


# ---------------------------------------------------------
# Feedback Endpoint
# ---------------------------------------------------------

@app.post("/feedback")
def submit_feedback(
    request: FeedbackRequest,
    db: Session = Depends(get_db),
):
    try:
        feedback = save_feedback(
            db,
            prediction_session_id=(
                request.prediction_id
            ),
            was_helpful=(
                request.was_helpful
            ),
            comment=request.comment,
        )

        return {
            "status": "feedback_saved",
            "feedback_id": str(
                feedback.id
            ),
            "prediction_id": str(
                feedback.prediction_session_id
            ),
            "was_helpful": (
                feedback.was_helpful
            ),
            "comment": feedback.comment,
            "created_at": (
                feedback.created_at
            ),
        }

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error