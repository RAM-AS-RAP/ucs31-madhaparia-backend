from contextlib import asynccontextmanager
from typing import Literal
import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine, get_db, init_db
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
    save_feedback,
    save_prediction_session,
)
from app.services.prediction import predict_top_conditions
from app.services.symptom_confirmation import confirm_symptoms

from app.core.auth import get_current_user
from app.core.security import create_access_token
from app.db.models import User
from app.services.auth import (
    authenticate_user,
    create_patient_user,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create any missing tables so a fresh database works out of the box
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
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

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
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
# Authentication Endpoints
# ---------------------------------------------------------

@app.post(
    "/auth/register",
    status_code=201,
)
def register_patient(
    request: RegisterRequest,
    db: Session = Depends(get_db),
):
    try:
        user = create_patient_user(
            db,
            email=str(request.email),
            password=request.password,
        )

        return {
            "id": str(user.id),
            "email": user.email,
            "role": user.role,
            "created_at": user.created_at,
        }

    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error


@app.post("/auth/login")
def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
):
    user = authenticate_user(
        db,
        email=str(request.email),
        password=request.password,
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )

    access_token = create_access_token(
        user_id=str(user.id),
        role=user.role,
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in_minutes": (
            settings.access_token_expire_minutes
        ),
    }


@app.get("/auth/me")
def get_authenticated_user(
    current_user: User = Depends(
        get_current_user
    ),
):
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "role": current_user.role,
        "created_at": current_user.created_at,
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
    current_user: User = Depends(get_current_user),
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
                    user_id=current_user.id,
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
                user_id=current_user.id,
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