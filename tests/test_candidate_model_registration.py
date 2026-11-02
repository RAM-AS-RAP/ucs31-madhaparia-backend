"""Verify registration metadata, inactive creation and transaction control."""

import copy
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.errors import ServiceNotReadyError
from app.db.models import ModelVersion
from app.db import register_candidate_model_version as registration


@pytest.fixture(scope="module")
def verified_values():
    return registration.build_candidate_registration_values()


@pytest.fixture
def prepared_values(verified_values, monkeypatch):
    values = copy.deepcopy(verified_values)
    monkeypatch.setattr(
        registration,
        "build_candidate_registration_values",
        lambda: copy.deepcopy(values),
    )
    return values


def test_metadata_comes_from_the_verified_candidate(verified_values):
    values = verified_values

    assert values["version"] == "candidate_v1_research"
    assert values["feature_count"] == 70
    assert values["class_count"] == 21
    assert values["training_samples"] == 334588
    assert values["validation_samples"] == 16741
    assert values["test_samples"] == 53272
    assert values["parameters"]["fitting_observations"] == 662036
    assert values["parameters"]["clinical_validation"] is False
    assert values["parameters"]["deployment_approved"] is False
    assert values["validation_metrics"]["sample_count_scope"] == "uncertainty_evaluation"
    assert values["test_metrics"]["sample_count_scope"] == "primary"


def test_new_registration_is_inactive_and_does_not_commit(prepared_values):
    db = MagicMock()
    db.scalar.return_value = None

    record = registration.register_candidate_model_version(db)

    assert record.version == "candidate_v1_research"
    assert record.is_active is False
    db.add.assert_called_once_with(record)
    db.flush.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.parametrize("active", [False, True])
def test_identical_existing_record_preserves_activation(prepared_values, active):
    existing = ModelVersion(**prepared_values, is_active=active)
    db = MagicMock()
    db.scalar.return_value = existing

    result = registration.register_candidate_model_version(db)

    assert result is existing
    assert result.is_active is active
    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("field", ["feature_count", "parameters", "test_metrics"])
def test_conflicting_metadata_is_not_overwritten(prepared_values, field):
    existing = ModelVersion(
        **copy.deepcopy(prepared_values),
        is_active=True,
    )

    if field == "feature_count":
        existing.feature_count = 66
    else:
        setattr(existing, field, {"different": True})

    before = copy.deepcopy(getattr(existing, field))
    db = MagicMock()
    db.scalar.return_value = existing

    with pytest.raises(
        registration.CandidateRegistrationConflictError,
        match=field,
    ):
        registration.register_candidate_model_version(db)

    assert getattr(existing, field) == before
    assert existing.is_active is True
    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.commit.assert_not_called()


def test_unavailable_candidate_prevents_database_access(monkeypatch):
    def fail():
        raise ServiceNotReadyError("Candidate unavailable")

    monkeypatch.setattr(registration, "build_candidate_registration_values", fail)
    db = MagicMock()

    with pytest.raises(ServiceNotReadyError):
        registration.register_candidate_model_version(db)

    db.scalar.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_called()


def active_ids(db):
    return set(
        db.scalars(
            select(ModelVersion.id).where(ModelVersion.is_active.is_(True))
        ).all()
    )


def test_postgresql_registration_is_inactive_idempotent_and_rollbackable(
    prepared_values,
):
    candidate_id = None

    with SessionLocal() as db:
        try:
            active_before = active_ids(db)
            assert active_before, "Expected the seeded active baseline model."

            first = registration.register_candidate_model_version(db)
            candidate_id = first.id

            assert candidate_id is not None
            assert first.is_active is False
            assert active_ids(db) == active_before

            # Reload JSONB metadata from PostgreSQL before comparing again.
            db.expire_all()
            second = registration.register_candidate_model_version(db)

            assert second.id == candidate_id
            assert second.is_active is False
            assert active_ids(db) == active_before

            matches = db.scalars(
                select(ModelVersion).where(
                    ModelVersion.model_name == prepared_values["model_name"],
                    ModelVersion.version == prepared_values["version"],
                )
            ).all()
            assert len(matches) == 1
        finally:
            db.rollback()

    with SessionLocal() as db:
        assert db.get(ModelVersion, candidate_id) is None


def test_postgresql_conflict_preserves_metadata_and_active_model(prepared_values):
    with SessionLocal() as db:
        try:
            active_before = active_ids(db)

            existing = ModelVersion(
                **copy.deepcopy(prepared_values),
                is_active=False,
            )
            existing.feature_count = 66
            db.add(existing)
            db.flush()

            with pytest.raises(registration.CandidateRegistrationConflictError):
                registration.register_candidate_model_version(db)

            db.refresh(existing)
            assert existing.feature_count == 66
            assert existing.is_active is False
            assert active_ids(db) == active_before
        finally:
            db.rollback()