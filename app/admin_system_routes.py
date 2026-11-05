"""Administrator tools. Frozen model definitions are deliberately read-only."""
import json
import unicodedata
import uuid
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import String, cast, func, literal, or_, select, text, union_all
from sqlalchemy.orm import Session, selectinload

from app.admin_user_routes import failure
from app.core.auth import require_admin
from app.core.database import get_db
from app.core.errors import ServiceNotReadyError
from app.db.models import (AdminAccountAction, AdminSystemActivity, DiseaseInformation,
    Feedback, ModelVersion, NlpMappingEvent, PredictionSession, Symptom, SymptomAlias, User)

def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/admin/system", tags=["System management"], dependencies=[Depends(require_admin), Depends(private_response)])
ROOT = Path(__file__).resolve().parent


def plain(value, *, multiline=False):
    value = unicodedata.normalize("NFC", value).strip()
    if any(unicodedata.category(char).startswith("C") and not (multiline and char in "\n\r\t") for char in value) or "<" in value or ">" in value:
        raise ValueError("Use plain text without control characters or HTML.")
    return value if multiline else " ".join(value.split())


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)


class SymptomUpdate(Revision):
    review_note: str = Field(max_length=1000)

    @field_validator("review_note")
    @classmethod
    def note(cls, value):
        return plain(value, multiline=True)


class AliasCreate(Revision):
    alias: str = Field(min_length=2, max_length=100)
    language: Literal["en", "sw"]

    @field_validator("alias")
    @classmethod
    def normalise(cls, value):
        value = plain(value).lower()
        if len(value) < 2 or not any(char.isalpha() for char in value):
            raise ValueError("An alias must contain symptom wording.")
        if set(value.split()) & {"no", "not", "without", "denies", "hakuna", "sina", "si"}:
            raise ValueError("Do not map negated wording to a present symptom.")
        return value


class DiseaseUpdate(Revision):
    display_name: str = Field(min_length=2, max_length=255)
    short_description: str = Field(min_length=10, max_length=2000)

    @field_validator("display_name", "short_description")
    @classmethod
    def content(cls, value):
        value = plain(value)
        if len(value) < 2:
            raise ValueError("Content is required.")
        return value

    @field_validator("short_description")
    @classmethod
    def description_length(cls, value):
        if len(value) < 10:
            raise ValueError("Provide a description of at least ten characters.")
        return value


class FeedbackReview(SymptomUpdate):
    review_status: Literal["pending", "reviewed", "resolved"]


def audit(db, admin, action, kind, target):
    db.add(AdminSystemActivity(actor_user_id=admin.id, action=action, target_type=kind, target_id=str(target)))


def lock(db, model, key, revision):
    column = model.condition if model is DiseaseInformation else model.id
    row = db.scalar(select(model).where(column == key).with_for_update().execution_options(populate_existing=True))
    if row is None:
        failure(404, "record_not_found", "This record no longer exists.")
    if row.revision != revision:
        failure(409, "record_changed", "This record changed. Refresh before saving.")
    return row


def page_rows(db, query, page, size=12):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return db.scalars(query.offset((page - 1) * size).limit(size)).all(), total


@router.get("/model")
def model_information(db: Session = Depends(get_db)):
    from app.ml.candidate_loader import CANDIDATE_MODEL_VERSION
    from app.ml.model_loader import V2_MODEL_VERSION, model_bundle
    from app.services.candidate_prediction import validate_candidate_registration
    from app.services.v2_prediction import get_v2_bundle

    records = db.scalars(select(ModelVersion).where(ModelVersion.is_active.is_(True))).all()
    if len(records) != 1:
        return {"state": "no_active_model" if not records else "multiple_active_models", "model": None}
    record = records[0]
    state = "ready"
    try:
        if record.version == CANDIDATE_MODEL_VERSION:
            validate_candidate_registration(record)
        elif record.version in ("1.0", V2_MODEL_VERSION):
            bundle = model_bundle if record.version == "1.0" else get_v2_bundle()
            if record.feature_count != len(bundle["features"]) or record.class_count != len(bundle["classes"]):
                raise ServiceNotReadyError("Model dimensions differ.")
            expected_artifact = "respiratory_random_forest_v1.joblib" if record.version == "1.0" else "rf_v2_23class_prototype.joblib"
            if record.artifact_filename != expected_artifact:
                raise ServiceNotReadyError("Model artifact differs.")
        else:
            state = "unsupported_version"
    except Exception:
        state = "runtime_unavailable"
    return {"state": state, "model": {
        "id": str(record.id), "name": record.model_name, "version": record.version,
        "purpose": record.purpose, "feature_count": record.feature_count, "class_count": record.class_count,
        "training_samples": record.training_samples, "validation_samples": record.validation_samples,
        "test_samples": record.test_samples, "created_at": record.created_at,
        "sample_count_definitions": record.parameters.get("sample_count_definitions", {}),
        "validation_metrics": record.validation_metrics, "test_metrics": record.test_metrics,
        "clinical_validation": False,
    }}


@router.get("/symptoms")
def symptoms(q: str = Query(default="", max_length=100), page: int = Query(default=1, ge=1, le=100000), db: Session = Depends(get_db)):
    query = select(Symptom).options(selectinload(Symptom.aliases)).order_by(Symptom.display_name, Symptom.id)
    if q.strip():
        query = query.where(or_(Symptom.code.icontains(q.strip(), autoescape=True), Symptom.display_name.icontains(q.strip(), autoescape=True),
            Symptom.aliases.any(SymptomAlias.alias.icontains(q.strip(), autoescape=True))))
    records, total = page_rows(db, query, page)
    return {"items": [symptom_record(row) for row in records], "total": total, "page": page}


@lru_cache(maxsize=1)
def baseline_aliases():
    # Seed files define the baseline recognition vocabulary; preserve it.
    result = set()
    for language in ("english", "swahili"):
        values = json.loads((ROOT / f"nlp/{language}_aliases.json").read_text(encoding="utf-8"))
        result.update((code, " ".join(value.lower().split())) for code, aliases in values.items() for value in aliases)
    return result


def protected_alias(alias, code):
    return (code, alias.strip().lower()) in baseline_aliases()


def symptom_record(row):
    return {"id": str(row.id), "code": row.code, "display_name": row.display_name,
        "is_red_flag": row.is_red_flag, "kind": row.kind, "features": row.features,
        "review_note": row.review_note, "revision": row.revision,
        "aliases": [{"id": str(alias.id), "alias": alias.alias, "language": alias.language,
            "protected": protected_alias(alias.alias, row.code)} for alias in sorted(row.aliases, key=lambda item: (item.language, item.alias))]}


@router.patch("/symptoms/{symptom_id}")
def update_symptom(symptom_id: uuid.UUID, payload: SymptomUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = lock(db, Symptom, symptom_id, payload.expected_revision)
    row.review_note = payload.review_note
    row.revision += 1
    audit(db, admin, "symptom_note_updated", "symptom", row.code)
    db.commit()
    return symptom_record(row)


@router.post("/symptoms/{symptom_id}/aliases", status_code=201)
def add_alias(symptom_id: uuid.UUID, payload: AliasCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    # All alias writes serialize across symptoms/languages to prevent ambiguous races.
    db.execute(text("SELECT pg_advisory_xact_lock(730073)"))
    row = lock(db, Symptom, symptom_id, payload.expected_revision)
    existing = db.scalars(select(SymptomAlias)).all()
    if any(" ".join(unicodedata.normalize("NFC", item.alias).lower().split()) == payload.alias for item in existing):
        failure(409, "alias_exists", "This wording is already mapped. Search the catalogue first.")
    db.add(SymptomAlias(symptom_id=row.id, alias=payload.alias, language=payload.language))
    row.revision += 1
    audit(db, admin, "alias_added", "symptom", row.code)
    db.commit()
    db.expire(row, ["aliases"])
    return symptom_record(row)


@router.delete("/symptoms/{symptom_id}/aliases/{alias_id}")
def remove_alias(symptom_id: uuid.UUID, alias_id: uuid.UUID, payload: Revision, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    db.execute(text("SELECT pg_advisory_xact_lock(730073)"))
    row = lock(db, Symptom, symptom_id, payload.expected_revision)
    alias = db.get(SymptomAlias, alias_id)
    if alias is None or alias.symptom_id != row.id:
        failure(404, "record_not_found", "This alias no longer exists.")
    if protected_alias(alias.alias, row.code):
        failure(403, "protected_definition", "Baseline aliases are protected. Custom aliases can be removed.")
    db.delete(alias)
    row.revision += 1
    audit(db, admin, "alias_removed", "symptom", row.code)
    db.commit()
    db.expire(row, ["aliases"])
    return symptom_record(row)


@router.get("/diseases")
def diseases(q: str = Query(default="", max_length=100), page: int = Query(default=1, ge=1, le=100000), db: Session = Depends(get_db)):
    query = select(DiseaseInformation).order_by(DiseaseInformation.display_name, DiseaseInformation.condition)
    if q.strip():
        query = query.where(or_(DiseaseInformation.condition.icontains(q.strip(), autoescape=True), DiseaseInformation.display_name.icontains(q.strip(), autoescape=True)))
    records, total = page_rows(db, query, page)
    return {"items": [disease_record(row) for row in records], "total": total, "page": page}


def disease_record(row):
    return {"condition": row.condition, "display_name": row.display_name, "short_description": row.short_description, "revision": row.revision}


@router.patch("/diseases")
def update_disease(condition: str = Query(max_length=255), payload: DiseaseUpdate = ..., db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = lock(db, DiseaseInformation, condition, payload.expected_revision)
    row.display_name, row.short_description = payload.display_name, payload.short_description
    row.revision += 1
    audit(db, admin, "disease_updated", "disease", condition)
    db.commit()
    return disease_record(row)


@router.get("/feedback")
def feedback(q: str = Query(default="", max_length=100), status: Literal["pending", "reviewed", "resolved"] | None = None,
             helpful: bool | None = None, page: int = Query(default=1, ge=1, le=100000), db: Session = Depends(get_db)):
    query = select(Feedback).order_by(Feedback.created_at.desc(), Feedback.id.desc())
    if q.strip():
        query = query.where(Feedback.comment.icontains(q.strip(), autoescape=True))
    if status:
        query = query.where(Feedback.review_status == status)
    if helpful is not None:
        query = query.where(Feedback.was_helpful == helpful)
    records, total = page_rows(db, query, page)
    return {"items": [feedback_record(row) for row in records], "total": total, "page": page}


def feedback_record(row):
    return {"id": str(row.id), "prediction_id": str(row.prediction_session_id), "was_helpful": row.was_helpful,
        "comment": row.comment, "created_at": row.created_at, "review_status": row.review_status,
        "review_note": row.review_note, "reviewed_by": str(row.reviewed_by) if row.reviewed_by else None,
        "reviewed_at": row.reviewed_at, "revision": row.revision}


@router.patch("/feedback/{feedback_id}")
def review_feedback(feedback_id: uuid.UUID, payload: FeedbackReview, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = lock(db, Feedback, feedback_id, payload.expected_revision)
    row.review_status, row.review_note = payload.review_status, payload.review_note
    row.reviewed_by = admin.id
    row.reviewed_at = datetime.now(timezone.utc)
    row.revision += 1
    audit(db, admin, "feedback_" + payload.review_status, "feedback", row.id)
    db.commit()
    return feedback_record(row)


@router.get("/nlp")
def diagnostics(source: Literal["events", "assessments"] = "events", kind: Literal["mapping_error", "no_match", "unmatched_wording"] | None = None,
                page: int = Query(default=1, ge=1, le=100000), db: Session = Depends(get_db)):
    if source == "events":
        query = select(NlpMappingEvent).order_by(NlpMappingEvent.created_at.desc(), NlpMappingEvent.id.desc())
        if kind:
            query = query.where(NlpMappingEvent.kind == kind)
        rows, total = page_rows(db, query, page)
        items = [{"id": str(row.id), "kind": row.kind, "created_at": row.created_at,
            "recognised_count": row.recognised_count, "unmatched_count": row.unmatched_count} for row in rows]
    else:
        # Filter/count in PostgreSQL, not only in a capped recent sample.
        query = select(PredictionSession).where(or_(
            PredictionSession.assessment["has_unmatched_wording"].as_boolean().is_(True),
            func.jsonb_array_length(func.coalesce(PredictionSession.assessment["unsupported_symptoms"], text("'[]'::jsonb"))) > 0,
            PredictionSession.status == "unsupported_input",
        )).order_by(PredictionSession.created_at.desc(), PredictionSession.id.desc())
        rows, total = page_rows(db, query, page)
        items = [{"id": str(row.id), "kind": "unsupported_input" if row.status == "unsupported_input" else "unmatched_wording",
            "created_at": row.created_at, "prediction_id": str(row.id),
            "unsupported_symptoms": (row.assessment or {}).get("unsupported_symptoms", []),
            "unmatched_wording": (row.assessment or {}).get("unmatched_wording", [])} for row in rows]
    return {"items": items, "total": total, "page": page}


@router.get("/activity")
def activity(page: int = Query(default=1, ge=1, le=100000), db: Session = Depends(get_db)):
    existing = select(cast(AdminAccountAction.id, String).label("id"), AdminAccountAction.actor_user_id,
        AdminAccountAction.action, literal("account").label("target_type"), cast(AdminAccountAction.target_user_id, String).label("target_id"), AdminAccountAction.created_at)
    current = select(cast(AdminSystemActivity.id, String), AdminSystemActivity.actor_user_id,
        AdminSystemActivity.action, AdminSystemActivity.target_type, AdminSystemActivity.target_id, AdminSystemActivity.created_at)
    combined = union_all(existing, current).subquery()
    total = db.scalar(select(func.count()).select_from(combined))
    rows = db.execute(select(combined).order_by(combined.c.created_at.desc(), combined.c.id.desc()).offset((page - 1) * 12).limit(12)).mappings().all()
    return {"items": [dict(row) for row in rows], "total": total, "page": page}
