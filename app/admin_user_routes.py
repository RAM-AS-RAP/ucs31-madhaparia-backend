"""Admin-only patient account management. No role promotion or admin removal."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.core.database import get_db
from app.core.profile_validation import normalise_full_name, normalise_phone_number
from app.core.security import hash_password, verify_password
from app.db.models import AdminAccountAction, Feedback, PasswordResetChallenge, PredictionSession, User
from app.services.auth import normalise_email
from app.services.account_status import account_can_sign_in

router = APIRouter(prefix="/admin/users", tags=["User management"])
NO_STORE = {"Cache-Control": "no-store"}


def failure(status, code, message, **headers):
    raise HTTPException(status, detail={"code": code, "message": message}, headers={**NO_STORE, **headers})


def public_account(user):
    return {
        "id": str(user.id), "email": user.email,
        "full_name": user.full_name, "phone_number": user.phone_number,
        "role": user.role, "is_active": user.is_active,
        "is_blocked": user.is_blocked, "created_at": user.created_at,
        "revision": user.auth_version,
    }


class NewAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = Field(max_length=254)
    full_name: str = Field(min_length=2, max_length=200)
    phone_number: str | None = Field(default=None, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name", mode="before")
    @classmethod
    def name(cls, value):
        name = normalise_full_name(value)
        if name is None:
            raise ValueError("Full name is required.")
        return name

    @field_validator("phone_number", mode="before")
    @classmethod
    def phone(cls, value):
        return normalise_phone_number(value)

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Password must not contain only spaces.")
        return value


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["block", "unblock", "activate", "deactivate"]
    expected_revision: int = Field(ge=0, strict=True)


class DeleteAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_email: EmailStr
    admin_password: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0, strict=True)


def audit(db, actor_id, target_id, action):
    db.add(AdminAccountAction(actor_user_id=actor_id, target_user_id=target_id, action=action))


def lock_patient(db, user_id, actor_id, revision):
    target = db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if target is None:
        failure(404, "account_not_found", "This account no longer exists.")
    if target.id == actor_id or target.role != "patient":
        failure(403, "protected_account", "Administrator accounts are protected from these actions.")
    if target.auth_version != revision:
        failure(409, "account_changed", "This account changed. Refresh before trying again.")
    return target


@router.get("")
def list_accounts(
    response: Response,
    q: str = Query(default="", max_length=100),
    role: Literal["patient", "admin"] | None = None,
    status: Literal["active", "inactive", "blocked"] | None = None,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=12, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    response.headers.update(NO_STORE)
    query = select(User)
    term = q.strip()
    if term:
        query = query.where(or_(User.email.icontains(term, autoescape=True), User.full_name.icontains(term, autoescape=True), User.phone_number.icontains(term, autoescape=True)))
    if role:
        query = query.where(User.role == role)
    if status == "active":
        query = query.where(User.is_active.is_(True), User.is_blocked.is_(False))
    elif status == "inactive":
        query = query.where(User.is_active.is_(False))
    elif status == "blocked":
        query = query.where(User.is_blocked.is_(True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    users = db.scalars(query.order_by(User.created_at.desc(), User.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"users": [public_account(user) for user in users], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
def add_account(payload: NewAccount, response: Response, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    response.headers.update(NO_STORE)
    user = User(email=normalise_email(str(payload.email)), full_name=payload.full_name,
                phone_number=payload.phone_number, password_hash=hash_password(payload.password),
                role="patient", is_active=True, is_blocked=False)
    try:
        db.add(user)
        db.flush()
        audit(db, admin.id, user.id, "create")
        db.commit()
    except IntegrityError:
        db.rollback()
        failure(409, "email_exists", "An account with this email already exists.")
    db.refresh(user)
    return public_account(user)


@router.patch("/{user_id}/status")
def change_status(user_id: uuid.UUID, payload: StatusChange, response: Response, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    response.headers.update(NO_STORE)
    target = lock_patient(db, user_id, admin.id, payload.expected_revision)
    field, value = {
        "block": ("is_blocked", True), "unblock": ("is_blocked", False),
        "activate": ("is_active", True), "deactivate": ("is_active", False),
    }[payload.action]
    if getattr(target, field) != value:
        setattr(target, field, value)
        target.auth_version += 1  # Old tokens and reset codes never revive on reactivation.
        audit(db, admin.id, target.id, payload.action)
    db.commit()
    db.refresh(target)
    return public_account(target)


@router.delete("/{user_id}")
def remove_account(user_id: uuid.UUID, payload: DeleteAccount, response: Response, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    response.headers.update(NO_STORE)
    version = admin.auth_version
    # Serialize password checks for this administrator, including failed attempts.
    actor = db.scalar(select(User).where(User.id == admin.id).with_for_update().execution_options(populate_existing=True))
    if actor is None or actor.role != "admin" or not account_can_sign_in(actor) or actor.auth_version != version:
        failure(401, "session_expired", "Please log in again.")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    attempts = db.scalar(select(func.count()).select_from(AdminAccountAction).where(
        AdminAccountAction.actor_user_id == actor.id,
        AdminAccountAction.action == "delete_denied", AdminAccountAction.created_at >= cutoff,
    ))
    if attempts >= 5:
        failure(429, "reauth_limited", "Too many password attempts. Try again in 15 minutes.", **{"Retry-After": "900"})
    if not verify_password(payload.admin_password, actor.password_hash):
        audit(db, actor.id, user_id, "delete_denied")
        db.commit()
        failure(403, "admin_password_invalid", "Your administrator password is incorrect.")
    target = lock_patient(db, user_id, actor.id, payload.expected_revision)
    if normalise_email(str(payload.confirmation_email)) != target.email:
        failure(400, "confirmation_mismatch", "Type the account email to confirm deletion.")
    session_ids = select(PredictionSession.id).where(PredictionSession.user_id == target.id)
    db.execute(delete(Feedback).where(Feedback.prediction_session_id.in_(session_ids)))
    db.execute(delete(PredictionSession).where(PredictionSession.user_id == target.id))
    db.execute(delete(PasswordResetChallenge).where(PasswordResetChallenge.user_id == target.id))
    audit(db, actor.id, target.id, "delete")
    db.execute(delete(User).where(User.id == target.id))
    db.commit()
    return {"status": "deleted", "user_id": str(user_id)}
