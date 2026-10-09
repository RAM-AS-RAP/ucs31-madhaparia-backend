from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ModelVersion


def get_active_model_version(
    db: Session,
) -> ModelVersion:
    active_versions = list(
        db.scalars(
            select(ModelVersion)
            .where(
                ModelVersion.is_active.is_(True)
            )
            .order_by(
                ModelVersion.created_at.desc()
            )
        ).all()
    )

    if not active_versions:
        raise RuntimeError(
            "No active machine-learning "
            "model version is configured."
        )

    if len(active_versions) > 1:
        raise RuntimeError(
            "Multiple active machine-learning "
            "model versions are configured."
        )

    return active_versions[0]