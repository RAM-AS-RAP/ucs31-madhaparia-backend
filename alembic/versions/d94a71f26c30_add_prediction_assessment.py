"""Add a nullable assessment snapshot to prediction sessions.

Revision ID: d94a71f26c30
Revises: a3f1c9d27b64
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d94a71f26c30"
down_revision = "a3f1c9d27b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prediction_sessions",
        sa.Column(
            "assessment",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("prediction_sessions", "assessment")