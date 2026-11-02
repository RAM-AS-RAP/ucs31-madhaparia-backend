"""Controlled candidate activation.

Importing this module does not activate anything.
The caller owns the transaction and must explicitly commit.

Application activation remains pending frontend readiness and a separate
activation decision.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ModelVersion
from app.db.register_candidate_model_version import (
    build_candidate_registration_values,
)


class CandidateActivationError(ValueError):
    """The registered models do not satisfy activation requirements."""


def activate_candidate_model_version(
    db: Session,
    *,
    expected_previous_version: str,
) -> ModelVersion:
    """Select the verified candidate without committing the transaction."""

    expected = build_candidate_registration_values()

    with db.no_autoflush:
        versions = list(
            db.scalars(
                select(ModelVersion)
                .order_by(ModelVersion.id)
                .with_for_update()
            ).all()
        )

        active = [version for version in versions if version.is_active]

        if len(active) != 1:
            raise CandidateActivationError(
                "Exactly one active model is required before activation."
            )

        if active[0].version != expected_previous_version:
            raise CandidateActivationError(
                "The active model changed from the expected previous version."
            )

        candidates = [
            version
            for version in versions
            if version.model_name == expected["model_name"]
            and version.version == expected["version"]
        ]

        if len(candidates) != 1:
            raise CandidateActivationError(
                "The candidate must be registered exactly once."
            )

        candidate = candidates[0]

        if candidate.is_active:
            raise CandidateActivationError(
                "The candidate is already active."
            )

        mismatches = [
            field
            for field, value in expected.items()
            if getattr(candidate, field) != value
        ]
        if mismatches:
            raise CandidateActivationError(
                "Candidate registration metadata differs: "
                + ", ".join(sorted(mismatches))
            )

        # Change activation flags only after every check passes.
        for version in versions:
            version.is_active = version.id == candidate.id

        db.flush()

    return candidate