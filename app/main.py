from contextlib import asynccontextmanager
from typing import Literal
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_admin
from app.core.config import settings
from app.core.database import check_schema, engine, get_db
from app.core.errors import ServiceNotReadyError
from app.core.security import create_access_token
from app.db.models import User
from app.ml.candidate_loader import CANDIDATE_MODEL_VERSION
from app.ml.model_loader import (
    V2_MODEL_VERSION,
    disease_classes,
    model,
    model_features,
)
from app.nlp.symptom_mapper import build_confirmation_output
from app.services.auth import authenticate_user, create_patient_user
from app.services.account_status import AccountAccessRestricted
from app.services.candidate_prediction import (
    assess_candidate_confirmation,
    build_independent_safety_assessment,
    validate_candidate_registration,
)
from app.services.candidate_symptom_mapping import CandidateMappingInputError
from app.services.health_guidance import (
    enrich_predictions,
    get_prediction_guidance,
    get_urgent_guidance,
)
from app.services.history import (
    get_any_prediction_session,
    get_prediction_session,
    list_all_feedback,
    list_all_prediction_sessions,
    list_prediction_sessions,
    save_feedback,
    save_prediction_session,
)
from app.services.model_versions import get_active_model_version
from app.services.prediction import predict_top_conditions
from app.services.symptom_confirmation import confirm_symptoms
from app.services.v2_prediction import (
    partition_v2_symptom_features,
    predict_v2_single,
)
from app.core.profile_validation import (
    normalise_full_name,
    normalise_phone_number,
)
from app.password_reset_routes import router as password_reset_router
from app.admin_user_routes import router as admin_user_router
from app.admin_system_routes import router as admin_system_router
from app.services.system_management import apply_disease_information, record_mapping_event

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database schema changes are managed through Alembic.
    check_schema()
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

allowed_origins = [
    origin.strip()
    for origin in settings.frontend_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

app.include_router(password_reset_router)
app.include_router(admin_user_router)
app.include_router(admin_system_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError):
    if request.url.path.startswith(("/auth/password-reset/", "/admin/")):
        # Never echo rejected codes or passwords in validation responses.
        return JSONResponse(
            status_code=422,
            content={"detail": [
                {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                for item in error.errors()
            ]},
            headers={"Cache-Control": "no-store"},
        )
    return await request_validation_exception_handler(request, error)


@app.exception_handler(ServiceNotReadyError)
def service_not_ready_handler(
    request: Request,
    error: ServiceNotReadyError,
):
    return JSONResponse(
        status_code=503,
        content={"detail": str(error)},
    )


# ---------------------------------------------------------
# Request Models
# ---------------------------------------------------------

MAX_SYMPTOM_TEXT_LENGTH = 2000
MAX_CONFIRMED_CODES = 50


class SymptomTextRequest(BaseModel):
    text: str = Field(max_length=MAX_SYMPTOM_TEXT_LENGTH)


class SymptomConfirmationRequest(BaseModel):
    text: str = Field(max_length=MAX_SYMPTOM_TEXT_LENGTH)
    confirmed_codes: list[str] = Field(max_length=MAX_CONFIRMED_CODES)


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age: int = Field(ge=0, le=120, strict=True)
    sex: Literal["M", "F"]
    text: str = Field(max_length=MAX_SYMPTOM_TEXT_LENGTH)
    confirmed_codes: list[str] = Field(max_length=MAX_CONFIRMED_CODES)


class FeedbackRequest(BaseModel):
    prediction_id: uuid.UUID
    was_helpful: bool
    comment: str | None = Field(default=None, max_length=1000)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    # Older API clients may omit these fields.
    # The new registration form will require a full name.
    full_name: str | None = None
    phone_number: str | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def validate_full_name(cls, value):
        return normalise_full_name(value)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone_number(cls, value):
        return normalise_phone_number(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if not value.strip():
            raise ValueError(
                "Password must not contain only spaces."
            )

        return value


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    phone_number: str | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def validate_full_name(cls, value):
        if value is None:
            raise ValueError("Full name cannot be empty.")

        return normalise_full_name(value)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone_number(cls, value):
        return normalise_phone_number(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


# ---------------------------------------------------------
# General Endpoints
# ---------------------------------------------------------

@app.get("/")
def root():
    return {
        "message": "Respiratory Health Awareness API is running.",
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
            "database": engine.dialect.name,
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


# ---------------------------------------------------------
# Authentication Endpoints
# ---------------------------------------------------------

@app.post("/auth/register", status_code=201)
def register_patient(
    request: RegisterRequest,
    db: Session = Depends(get_db),
):
    profile = {}

    if request.full_name is not None:
        profile["full_name"] = request.full_name

    if request.phone_number is not None:
        profile["phone_number"] = request.phone_number

    try:
        user = create_patient_user(
            db,
            email=str(request.email),
            password=request.password,
            **profile,
        )

        return {
            "id": str(user.id),
            "email": user.email,
            "full_name": getattr(user, "full_name", None),
            "phone_number": getattr(user, "phone_number", None),
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
    try:
        user = authenticate_user(
            db,
            email=str(request.email),
            password=request.password,
        )
    except AccountAccessRestricted as error:
        raise HTTPException(
            status_code=403,
            detail={"code": error.code, "message": str(error)},
            headers={"Cache-Control": "no-store"},
        ) from error

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        user_id=str(user.id),
        role=user.role,
        auth_version=getattr(user, "auth_version", 0),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in_minutes": settings.access_token_expire_minutes,
    }


@app.get("/auth/me")
def get_authenticated_user(
    current_user: User = Depends(get_current_user),
):
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "full_name": getattr(current_user, "full_name", None),
        "phone_number": getattr(current_user, "phone_number", None),
        "role": current_user.role,
        "created_at": current_user.created_at,
    }


@app.patch("/auth/me")
def update_authenticated_user(
    request: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    changes = request.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide a full name or phone number to update.",
        )

    if "full_name" in changes:
        current_user.full_name = changes["full_name"]

    if "phone_number" in changes:
        current_user.phone_number = changes["phone_number"]

    db.commit()
    db.refresh(current_user)

    return get_authenticated_user(current_user)


# ---------------------------------------------------------
# NLP Endpoints
# ---------------------------------------------------------

@app.post("/nlp/map")
def map_symptoms(
    request: SymptomTextRequest,
    db: Session = Depends(get_db),
):
    try:
        output = build_confirmation_output(request.text, db)
    except Exception:
        record_mapping_event(db=db)
        raise
    record_mapping_event(output, db=db)
    return output


@app.post("/symptoms/confirm")
def confirm_recognised_symptoms(
    request: SymptomConfirmationRequest,
    db: Session = Depends(get_db),
):
    try:
        return confirm_symptoms(
            text=request.text,
            confirmed_codes=request.confirmed_codes,
            db=db,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

@app.post("/safety/reviews")
def create_safety_review(
    request: SymptomConfirmationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        confirmation = confirm_symptoms(
            text=request.text,
            confirmed_codes=request.confirmed_codes,
            db=db,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    if not confirmation["has_red_flag"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "A safety review requires at least one "
                "recognised and confirmed red-flag symptom."
            ),
        )

    # This endpoint never selects or runs a prediction model.
    assessment = build_independent_safety_assessment(confirmation)
    guidance = get_urgent_guidance()

    session = save_prediction_session(
        db,
        user_id=current_user.id,
        age=None,
        sex=None,
        input_text=request.text,
        confirmed_codes=confirmation["confirmed_codes"],
        model_features=confirmation["model_features"],
        status="urgent_review",
        has_red_flag=True,
        predictions=[],
        model_version_id=None,
        assessment=assessment,
    )

    return {
        "prediction_id": str(session.id),
        "status": "urgent_review",
        "age": None,
        "sex": None,
        "confirmed_codes": confirmation["confirmed_codes"],
        "model_features": confirmation["model_features"],
        "has_red_flag": True,
        "red_flag_symptoms": confirmation["red_flag_symptoms"],
        "predictions": [],
        "assessment": assessment,
        **guidance,
    }

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
            db=db,
        )

        # Independent safety checks run before model selection.
        if confirmation["has_red_flag"]:
            urgent_guidance = get_urgent_guidance()
            safety_assessment = build_independent_safety_assessment(
                confirmation
            )

            prediction_session = save_prediction_session(
                db,
                user_id=current_user.id,
                age=request.age,
                sex=request.sex,
                input_text=request.text,
                confirmed_codes=confirmation["confirmed_codes"],
                model_features=confirmation["model_features"],
                status="urgent_review",
                has_red_flag=True,
                predictions=[],
                model_version_id=None,
                assessment=safety_assessment,
            )

            return {
                "prediction_id": str(prediction_session.id),
                "status": "urgent_review",
                "age": request.age,
                "sex": request.sex,
                "confirmed_codes": confirmation["confirmed_codes"],
                "model_features": confirmation["model_features"],
                "has_red_flag": True,
                "red_flag_symptoms": confirmation["red_flag_symptoms"],
                "predictions": [],
                "assessment": safety_assessment,
                **urgent_guidance,
            }

        active_model_version = get_active_model_version(db)

        # Candidate assessment route.
        if active_model_version.version == CANDIDATE_MODEL_VERSION:
            try:
                validate_candidate_registration(active_model_version)

                candidate = assess_candidate_confirmation(
                    age=request.age,
                    sex=request.sex,
                    confirmation=confirmation,
                )

                candidate = {**candidate, "predictions": apply_disease_information(candidate["predictions"], db)}

                if candidate["status"] == "urgent_review":
                    guidance = get_urgent_guidance()
                else:
                    guidance = get_prediction_guidance()
                    guidance["confidence_explanation"] = (
                        candidate["assessment"]["score_note"]
                    )

                prediction_session = save_prediction_session(
                    db,
                    user_id=current_user.id,
                    age=request.age,
                    sex=request.sex,
                    input_text=request.text,
                    confirmed_codes=candidate["confirmed_codes"],
                    model_features=candidate["model_features"],
                    status=candidate["status"],
                    has_red_flag=candidate["has_red_flag"],
                    predictions=candidate["predictions"],
                    model_version_id=active_model_version.id,
                    assessment=candidate["assessment"],
                )

                return {
                    "prediction_id": str(prediction_session.id),
                    "status": candidate["status"],
                    "model_version": candidate["model_version"],
                    "age": request.age,
                    "sex": request.sex,
                    "confirmed_codes": candidate["confirmed_codes"],
                    "model_features": candidate["model_features"],
                    "has_red_flag": candidate["has_red_flag"],
                    "feature_count": candidate["feature_count"],
                    "predictions": candidate["predictions"],
                    "assessment": candidate["assessment"],
                    **guidance,
                }

            except CandidateMappingInputError as error:
                raise HTTPException(
                    status_code=400,
                    detail=str(error),
                ) from error

            except ServiceNotReadyError as error:
                raise HTTPException(
                    status_code=503,
                    detail="The candidate assessment service is unavailable.",
                ) from error

            except Exception as error:
                db.rollback()
                raise HTTPException(
                    status_code=500,
                    detail="The candidate assessment could not be completed.",
                ) from error

        # Existing V1/V2 prediction routes.
        unsupported_model_features = []

        if active_model_version.version == V2_MODEL_VERSION:
            partition = partition_v2_symptom_features(
                confirmation["model_features"]
            )
            unsupported_model_features = partition["unsupported_features"]

            if not partition["has_supported_features"]:
                raise ValueError(
                    "There is not enough V2-supported symptom "
                    "information to make a prediction. "
                    "Please provide at least one supported "
                    "confirmed symptom."
                )

            prediction_result = predict_v2_single(
                age=request.age,
                sex=request.sex,
                symptom_features=partition["supported_features"],
            )

        elif active_model_version.version == "1.0":
            prediction_result = predict_top_conditions(
                age=request.age,
                sex=request.sex,
                symptom_features=confirmation["model_features"],
                top_k=1,
            )

        else:
            raise ServiceNotReadyError(
                "The active model version is not supported "
                "by the prediction service."
            )

        enriched_predictions = enrich_predictions(
            prediction_result["predictions"]
        )
        enriched_predictions = apply_disease_information(enriched_predictions, db)
        guidance = get_prediction_guidance()

        prediction_session = save_prediction_session(
            db,
            user_id=current_user.id,
            age=request.age,
            sex=request.sex,
            input_text=request.text,
            confirmed_codes=confirmation["confirmed_codes"],
            model_features=confirmation["model_features"],
            status="prediction_complete",
            has_red_flag=False,
            predictions=enriched_predictions,
            model_version_id=active_model_version.id,
        )

        return {
            "prediction_id": str(prediction_session.id),
            "status": "prediction_complete",
            "model_version": active_model_version.version,
            "age": request.age,
            "sex": request.sex,
            "confirmed_codes": confirmation["confirmed_codes"],
            "model_features": confirmation["model_features"],
            "has_red_flag": False,
            "feature_count": prediction_result["feature_count"],
            "predictions": enriched_predictions,
            **(
                {
                    "uncertain": prediction_result["uncertain"],
                    "uncertainty_reasons": prediction_result[
                        "uncertainty_reasons"
                    ],
                    "uncertainty_message": prediction_result[
                        "uncertainty_message"
                    ],
                    "score_note": prediction_result["score_note"],
                    "unsupported_model_features": unsupported_model_features,
                }
                if active_model_version.version == V2_MODEL_VERSION
                else {}
            ),
            **guidance,
        }

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


# ---------------------------------------------------------
# Patient History Endpoints
# ---------------------------------------------------------

@app.get("/history")
def get_patient_history(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sessions = list_prediction_sessions(
        db,
        user_id=current_user.id,
        limit=limit,
    )

    return {
        "count": len(sessions),
        "history": [
            {
                "prediction_id": str(session.id),
                "age": session.age,
                "sex": session.sex,
                "input_text": session.input_text,
                "confirmed_codes": session.confirmed_codes,
                "status": session.status,
                "has_red_flag": session.has_red_flag,
                "predictions": session.predictions,
                "assessment": session.assessment,
                "created_at": session.created_at,
            }
            for session in sessions
        ],
    }


# ---------------------------------------------------------
# Admin Endpoints
# ---------------------------------------------------------

@app.get("/admin/history")
def get_admin_history(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    sessions = list_all_prediction_sessions(db, limit=limit)

    return {
        "count": len(sessions),
        "history": [
            {
                "prediction_id": str(session.id),
                "user_id": (
                    str(session.user_id)
                    if session.user_id
                    else None
                ),
                "age": session.age,
                "sex": session.sex,
                "input_text": session.input_text,
                "confirmed_codes": session.confirmed_codes,
                "status": session.status,
                "has_red_flag": session.has_red_flag,
                "predictions": session.predictions,
                "assessment": session.assessment,
                "created_at": session.created_at,
            }
            for session in sessions
        ],
    }


@app.get("/admin/history/{prediction_id}")
def get_admin_history_item(
    prediction_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    session = get_any_prediction_session(
        db,
        prediction_id=prediction_id,
    )

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Prediction session was not found.",
        )

    return {
        "prediction_id": str(session.id),
        "user_id": str(session.user_id) if session.user_id else None,
        "age": session.age,
        "sex": session.sex,
        "input_text": session.input_text,
        "confirmed_codes": session.confirmed_codes,
        "status": session.status,
        "has_red_flag": session.has_red_flag,
        "predictions": session.predictions,
        "assessment": session.assessment,
        "created_at": session.created_at,
    }


@app.get("/admin/feedback")
def get_admin_feedback(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    feedback_items = list_all_feedback(db, limit=limit)

    return {
        "count": len(feedback_items),
        "feedback": [
            {
                "feedback_id": str(item.id),
                "prediction_id": str(item.prediction_session_id),
                "was_helpful": item.was_helpful,
                "comment": item.comment,
                "created_at": item.created_at,
            }
            for item in feedback_items
        ],
    }


@app.get("/history/{prediction_id}")
def get_patient_history_item(
    prediction_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = get_prediction_session(
        db,
        prediction_id=prediction_id,
        user_id=current_user.id,
    )

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Prediction session was not found.",
        )

    return {
        "prediction_id": str(session.id),
        "age": session.age,
        "sex": session.sex,
        "input_text": session.input_text,
        "confirmed_codes": session.confirmed_codes,
        "status": session.status,
        "has_red_flag": session.has_red_flag,
        "predictions": session.predictions,
        "assessment": session.assessment,
        "created_at": session.created_at,
    }


# ---------------------------------------------------------
# Feedback Endpoint
# ---------------------------------------------------------

@app.post("/feedback")
def submit_feedback(
    request: FeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        feedback = save_feedback(
            db,
            prediction_session_id=request.prediction_id,
            user_id=current_user.id,
            was_helpful=request.was_helpful,
            comment=request.comment,
        )

        return {
            "status": "feedback_saved",
            "feedback_id": str(feedback.id),
            "prediction_id": str(feedback.prediction_session_id),
            "was_helpful": feedback.was_helpful,
            "comment": feedback.comment,
            "created_at": feedback.created_at,
        }

    except LookupError as error:
        raise HTTPException(
            status_code=404,
            detail="Prediction session was not found.",
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
