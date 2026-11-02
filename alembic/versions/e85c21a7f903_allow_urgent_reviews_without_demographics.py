"""Allow urgent reviews without age or sex.

Revision ID: e85c21a7f903
Revises: d94a71f26c30
"""

from alembic import op
import sqlalchemy as sa


revision = "e85c21a7f903"
down_revision = "d94a71f26c30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "prediction_sessions",
        "age",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "prediction_sessions",
        "sex",
        existing_type=sa.String(length=1),
        nullable=True,
    )


def downgrade() -> None:
    missing_demographics = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM prediction_sessions "
            "WHERE age IS NULL OR sex IS NULL"
        )
    ).scalar_one()

    if missing_demographics:
        raise RuntimeError(
            "Cannot restore required demographic fields while "
            "saved reviews contain missing age or sex."
        )

    op.alter_column(
        "prediction_sessions",
        "sex",
        existing_type=sa.String(length=1),
        nullable=False,
    )
    op.alter_column(
        "prediction_sessions",
        "age",
        existing_type=sa.Integer(),
        nullable=False,
    )