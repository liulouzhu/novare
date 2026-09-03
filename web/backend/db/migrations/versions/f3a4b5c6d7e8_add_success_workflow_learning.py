"""add successful workflow learning and create-Skill proposals

Revision ID: f3a4b5c6d7e8
Revises: f2a3b4c5d6e7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "successful_workflow_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("turn_id", sa.String(length=32), nullable=False),
        sa.Column("task_signature", sa.String(length=64), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("workflow_family", sa.String(length=160), nullable=False),
        sa.Column("workflow_name", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("when_to_use", sa.Text(), server_default="", nullable=False),
        sa.Column("prerequisites", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("decision_points", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("pitfalls", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("verification_steps", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("tool_sequence", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("existing_skill_match", sa.String(length=80), nullable=True),
        sa.Column("reusability", sa.Float(), server_default="0", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("complexity_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("verification_status", sa.String(length=40), server_default="", nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("model_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("environment_fingerprint", sa.String(length=64), server_default="", nullable=False),
        sa.Column("eligible_for_learning", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=255), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "run_id", name="uq_success_workflow_user_run"),
    )
    op.create_index("idx_success_workflows_key", "successful_workflow_observations", ["user_id", "workflow_key"])
    op.create_index("idx_success_workflows_eligible", "successful_workflow_observations", ["user_id", "eligible_for_learning"])
    op.create_index("ix_successful_workflow_observations_user_id", "successful_workflow_observations", ["user_id"])

    op.add_column("skill_proposals", sa.Column("candidate_type", sa.String(length=32), server_default="reflection", nullable=False))
    op.add_column("skill_proposals", sa.Column("proposal_type", sa.String(length=16), server_default="patch", nullable=False))
    op.create_check_constraint("ck_skill_proposal_candidate_type", "skill_proposals", "candidate_type IN ('reflection', 'successful_workflow')")
    op.create_check_constraint("ck_skill_proposal_type", "skill_proposals", "proposal_type IN ('patch', 'create')")


def downgrade() -> None:
    op.drop_constraint("ck_skill_proposal_type", "skill_proposals", type_="check")
    op.drop_constraint("ck_skill_proposal_candidate_type", "skill_proposals", type_="check")
    op.drop_column("skill_proposals", "proposal_type")
    op.drop_column("skill_proposals", "candidate_type")
    op.drop_table("successful_workflow_observations")
