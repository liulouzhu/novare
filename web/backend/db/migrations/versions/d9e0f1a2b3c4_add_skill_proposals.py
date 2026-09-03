"""add Skill diff proposals, backups, and audit trail

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bring the observation tables in line with their ORM user_id indexes.
    # This lives here so installations that already applied c8 also receive it.
    op.create_index("ix_reflection_resolutions_user_id", "reflection_resolutions", ["user_id"])
    op.create_index("ix_evolution_experiences_user_id", "evolution_experiences", ["user_id"])

    op.create_table(
        "skill_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_key", sa.String(length=64), nullable=False),
        sa.Column("skill_name", sa.String(length=80), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("base_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("candidate_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("proposed_content", sa.Text(), server_default="", nullable=False),
        sa.Column("unified_diff", sa.Text(), server_default="", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("rationale", sa.Text(), server_default="", nullable=False),
        sa.Column("risk_level", sa.String(length=20), server_default="unknown", nullable=False),
        sa.Column("test_plan", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="generating", nullable=False),
        sa.Column("generated_by_model", sa.String(length=255), server_default="", nullable=False),
        sa.Column("generation_error", sa.Text(), server_default="", nullable=False),
        sa.Column("approval_comment", sa.Text(), server_default="", nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_content_sha256", sa.String(length=64), server_default="", nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('generating', 'draft', 'approved', 'rejected', 'applying', "
            "'applied', 'stale', 'failed', 'rolled_back')",
            name="ck_skill_proposal_status",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'unknown')",
            name="ck_skill_proposal_risk",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_skill_proposals_user_status", "skill_proposals", ["user_id", "status"])
    op.create_index("idx_skill_proposals_lesson", "skill_proposals", ["user_id", "lesson_key"])
    op.create_index("ix_skill_proposals_user_id", "skill_proposals", ["user_id"])

    op.create_table(
        "skill_proposal_backups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_existed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("backup_path", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["skill_proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", name="uq_skill_proposal_backup_proposal"),
    )
    op.create_index("ix_skill_proposal_backups_user_id", "skill_proposal_backups", ["user_id"])

    op.create_table(
        "skill_proposal_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=20), server_default="", nullable=False),
        sa.Column("to_status", sa.String(length=20), server_default="", nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["skill_proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_skill_proposal_audits_proposal", "skill_proposal_audits", ["proposal_id", "created_at"])
    op.create_index("idx_skill_proposal_audits_user", "skill_proposal_audits", ["user_id", "created_at"])
    op.create_index("ix_skill_proposal_audits_user_id", "skill_proposal_audits", ["user_id"])


def downgrade() -> None:
    op.drop_table("skill_proposal_audits")
    op.drop_table("skill_proposal_backups")
    op.drop_table("skill_proposals")
    op.drop_index("ix_evolution_experiences_user_id", table_name="evolution_experiences")
    op.drop_index("ix_reflection_resolutions_user_id", table_name="reflection_resolutions")
