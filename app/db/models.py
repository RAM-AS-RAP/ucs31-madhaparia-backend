import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PredictionSession(Base):
    __tablename__ = "prediction_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    age: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    sex: Mapped[str] = mapped_column(
        String(1),
        nullable=False,
    )

    input_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    confirmed_codes: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
    )

    model_features: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    has_red_flag: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    predictions: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    feedback_items: Mapped[list["Feedback"]] = relationship(
        back_populates="prediction_session",
        cascade="all, delete-orphan",
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    prediction_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "prediction_sessions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    was_helpful: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    prediction_session: Mapped["PredictionSession"] = relationship(
        back_populates="feedback_items",
    )