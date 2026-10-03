import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        CheckConstraint(
            "role IN ('patient', 'admin')",
            name="ck_users_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="patient",
        server_default="patient",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    prediction_sessions: Mapped[list["PredictionSession"]] = relationship(
        back_populates="user",
    )


class PredictionSession(Base):
    __tablename__ = "prediction_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_prediction_sessions_user_id_users",
        ),
        nullable=True,
        index=True,
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

    user: Mapped[User | None] = relationship(
        back_populates="prediction_sessions",
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