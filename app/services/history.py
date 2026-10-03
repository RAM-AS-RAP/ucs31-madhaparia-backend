import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Feedback, PredictionSession


def save_prediction_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    age: int,
    sex: str,
    input_text: str,
    confirmed_codes: list[str],
    model_features: list[str],
    status: str,
    has_red_flag: bool,
    predictions: list[dict],
) -> PredictionSession:
    session = PredictionSession(
        user_id=user_id,
        age=age,
        sex=sex,
        input_text=input_text,
        confirmed_codes=confirmed_codes,
        model_features=model_features,
        status=status,
        has_red_flag=has_red_flag,
        predictions=predictions,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    return session


def get_prediction_session(
    db: Session,
    prediction_id: uuid.UUID,
) -> PredictionSession | None:
    return db.get(PredictionSession, prediction_id)


def list_prediction_sessions(
    db: Session,
    limit: int = 20,
) -> list[PredictionSession]:
    statement = (
        select(PredictionSession)
        .order_by(PredictionSession.created_at.desc())
        .limit(limit)
    )

    return list(db.scalars(statement).all())


def save_feedback(
    db: Session,
    *,
    prediction_session_id: uuid.UUID,
    was_helpful: bool,
    comment: str | None,
) -> Feedback:
    prediction_session = db.get(
        PredictionSession,
        prediction_session_id,
    )

    if prediction_session is None:
        raise ValueError(
            "Prediction session was not found."
        )

    existing_feedback = db.scalar(
        select(Feedback).where(
            Feedback.prediction_session_id
            == prediction_session_id
        )
    )

    if existing_feedback is not None:
        raise ValueError(
            "Feedback has already been submitted "
            "for this prediction session."
        )

    feedback = Feedback(
        prediction_session_id=prediction_session_id,
        was_helpful=was_helpful,
        comment=comment,
    )

    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    return feedback