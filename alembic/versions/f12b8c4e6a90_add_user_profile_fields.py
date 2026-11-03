"""Add optional user profile fields.

Revision ID: f12b8c4e6a90
Revises: e85c21a7f903
"""

from alembic import op
import sqlalchemy as sa


revision = "f12b8c4e6a90"
down_revision = "e85c21a7f903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "full_name",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "phone_number",
            sa.String(length=16),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "phone_number")
    op.drop_column("users", "full_name")