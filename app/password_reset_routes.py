import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import email_delivery
from app.services.password_reset import (
    CODE_TTL_SECONDS,
    RESEND_COOLDOWN_SECONDS,
    PasswordResetError,
    complete_password_reset,
    deliver_password_changed_notice,
    deliver_reset_code,
    request_password_reset,
)


router = APIRouter(prefix="/auth/password-reset", tags=["Password recovery"])
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    language: Literal["en", "sw"] = "en"


class PasswordResetConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: uuid.UUID
    code: str = Field(pattern=r"^[0-9]{6}$", min_length=6, max_length=6)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def nonblank_password(cls, value):
        if not value.strip():
            raise ValueError("Password must not contain only spaces.")
        return value


class PasswordResetRequested(BaseModel):
    challenge_id: uuid.UUID
    expires_in_seconds: int
    resend_after_seconds: int
    message: str


class PasswordResetCompleted(BaseModel):
    status: Literal["password_reset"]
    message: str


def _http_error(error: PasswordResetError) -> HTTPException:
    headers = dict(NO_STORE)
    if error.retry_after:
        headers["Retry-After"] = str(error.retry_after)
    return HTTPException(
        status_code=error.status,
        detail={"code": error.code, "message": str(error)},
        headers=headers,
    )


@router.post("/request", status_code=202, response_model=PasswordResetRequested)
def request_code(
    payload: PasswordResetRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    try:
        email_delivery.ensure_email_configured()
    except email_delivery.EmailDeliveryUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail={"code": "email_unavailable", "message": "Password recovery is temporarily unavailable."},
            headers=NO_STORE,
        ) from error
    try:
        issued = request_password_reset(
            db, str(payload.email), payload.language,
            request.client.host if request.client else "unknown",
        )
    except PasswordResetError as error:
        raise _http_error(error) from error

    background.add_task(deliver_reset_code, issued)
    return {
        "challenge_id": issued.challenge_id,
        "expires_in_seconds": CODE_TTL_SECONDS,
        "resend_after_seconds": RESEND_COOLDOWN_SECONDS,
        "message": "If an account exists for that email, a password-reset code will be sent.",
    }


@router.post("/confirm", response_model=PasswordResetCompleted)
def confirm_code(
    payload: PasswordResetConfirmation,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    response.headers.update(NO_STORE)
    try:
        completed = complete_password_reset(
            db, payload.challenge_id, payload.code, payload.new_password,
            request.client.host if request.client else "unknown",
        )
    except PasswordResetError as error:
        raise _http_error(error) from error
    background.add_task(deliver_password_changed_notice, completed)
    return {"status": "password_reset", "message": "Your password was reset. Please log in."}
