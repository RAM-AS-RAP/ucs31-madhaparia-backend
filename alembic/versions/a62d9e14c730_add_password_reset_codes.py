"""Add password reset challenges, rate limits and session revocation.

Revision ID: a62d9e14c730
Revises: f12b8c4e6a90
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a62d9e14c730"
down_revision = "f12b8c4e6a90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "password_reset_challenges",
        sa.Column("email_key", sa.String(64), primary_key=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code_digest", sa.String(64), nullable=True),
        sa.Column("auth_version", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("attempts >= 0", name="ck_password_reset_attempts"),
    )
    op.create_table(
        "password_reset_rate_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("request_count >= 0", name="ck_password_reset_request_count"),
    )


def downgrade() -> None:
    # Removing the version check would make previously revoked JWTs usable.
    versions_changed = op.get_bind().execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM users WHERE auth_version > 0)")
    ).scalar_one()
    if versions_changed:
        raise RuntimeError(
            "Cannot remove session revocation after password resets. "
            "Expire all previously issued tokens and plan the rollback first."
        )
    op.drop_table("password_reset_rate_limits")
    op.drop_table("password_reset_challenges")
    op.drop_column("users", "auth_version")
