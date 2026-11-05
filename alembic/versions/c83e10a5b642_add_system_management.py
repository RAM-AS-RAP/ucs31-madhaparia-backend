"""Add administrator content management, feedback reviews and diagnostics.

Revision ID: c83e10a5b642
Revises: d7a42f9b6e10
"""
import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c83e10a5b642"
down_revision = "d7a42f9b6e10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("symptoms", sa.Column("review_note", sa.Text(), nullable=False, server_default=""))
    op.add_column("symptoms", sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
    for column in [
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    ]:
        op.add_column("feedback", column)
    op.create_check_constraint("ck_feedback_review_status", "feedback", "review_status IN ('pending', 'reviewed', 'resolved')")
    disease = op.create_table("disease_information",
        sa.Column("condition", sa.String(255), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("short_description", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
    source = Path(__file__).resolve().parents[2] / "app/data/condition_information.json"
    records = json.loads(source.read_text(encoding="utf-8"))
    op.bulk_insert(disease, [{"condition": key, **value} for key, value in records.items()])
    op.create_table("admin_system_activity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_admin_system_activity_actor_user_id", "admin_system_activity", ["actor_user_id"])
    op.create_table("nlp_mapping_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("recognised_count", sa.Integer(), nullable=False),
        sa.Column("unmatched_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_nlp_mapping_events_created_at", "nlp_mapping_events", ["created_at"])


def downgrade():
    op.drop_table("nlp_mapping_events")
    op.drop_table("admin_system_activity")
    op.drop_table("disease_information")
    op.drop_constraint("ck_feedback_review_status", "feedback", type_="check")
    for column in ["revision", "reviewed_at", "reviewed_by", "review_note", "review_status"]:
        op.drop_column("feedback", column)
    op.drop_column("symptoms", "revision")
    op.drop_column("symptoms", "review_note")
