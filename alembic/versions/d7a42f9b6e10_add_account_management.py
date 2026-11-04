"""Add account availability and administrator action references.

Revision ID: d7a42f9b6e10
Revises: a62d9e14c730
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d7a42f9b6e10"
down_revision = "a62d9e14c730"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "admin_account_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_account_actions_actor_user_id", "admin_account_actions", ["actor_user_id"])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM users WHERE is_active = false OR is_blocked = true)")):
        raise RuntimeError("Restore restricted accounts deliberately before removing account-status enforcement.")
    op.drop_index("ix_admin_account_actions_actor_user_id", table_name="admin_account_actions")
    op.drop_table("admin_account_actions")
    op.drop_column("users", "is_blocked")
    op.drop_column("users", "is_active")
