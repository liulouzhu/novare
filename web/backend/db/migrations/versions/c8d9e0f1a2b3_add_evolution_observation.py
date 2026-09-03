"""add observation-only self-evolution tables

Revision ID: c8d9e0f1a2b3
Revises: b2c3d4e5f6g7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reflection_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("turn_id", sa.String(length=32), nullable=False),
        sa.Column("reflection_id", sa.String(length=64), nullable=False),
        sa.Column("task_signature", sa.String(length=64), nullable=False),
        sa.Column("trigger", sa.String(length=64), nullable=False),
        sa.Column("failure_type", sa.String(length=80), server_default="", nullable=False),
        sa.Column("failed_tool", sa.String(length=128), server_default="", nullable=False),
        sa.Column("error_code", sa.String(length=80), server_default="", nullable=False),
        sa.Column("diagnosis", sa.Text(), server_default="", nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("revised_plan", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("suggested_next_action", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="uncertain", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("evidence_event_ids", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'helpful', 'ineffective', 'harmful', 'uncertain')",
            name="ck_reflection_resolution_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "reflection_id", name="uq_resolution_user_reflection"),
    )
    op.create_index("idx_reflection_resolutions_user_status", "reflection_resolutions", ["user_id", "status"])
    op.create_index("idx_reflection_resolutions_run", "reflection_resolutions", ["run_id"])

    op.create_table(
        "evolution_experiences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reflection_resolution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("reflection_id", sa.String(length=64), nullable=False),
        sa.Column("task_signature", sa.String(length=64), nullable=False),
        sa.Column("lesson_key", sa.String(length=64), nullable=False),
        sa.Column("experience_type", sa.String(length=40), server_default="failure_lesson", nullable=False),
        sa.Column("trigger", sa.String(length=64), nullable=False),
        sa.Column("failure_type", sa.String(length=80), server_default="", nullable=False),
        sa.Column("failed_tool", sa.String(length=128), server_default="", nullable=False),
        sa.Column("error_code", sa.String(length=80), server_default="", nullable=False),
        sa.Column("generalized_lesson", sa.Text(), nullable=False),
        sa.Column("resolution_status", sa.String(length=20), nullable=False),
        sa.Column("resolution_confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("model_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("environment_fingerprint", sa.String(length=64), server_default="", nullable=False),
        sa.Column("eligible_for_learning", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["reflection_resolution_id"], ["reflection_resolutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "reflection_id", name="uq_experience_user_reflection"),
    )
    op.create_index("idx_evolution_experiences_lesson", "evolution_experiences", ["user_id", "lesson_key"])
    op.create_index("idx_evolution_experiences_status", "evolution_experiences", ["user_id", "resolution_status"])


def downgrade() -> None:
    op.drop_table("evolution_experiences")
    op.drop_table("reflection_resolutions")
