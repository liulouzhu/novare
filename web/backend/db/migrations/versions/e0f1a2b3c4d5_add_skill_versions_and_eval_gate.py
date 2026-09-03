"""add Skill versions, execution attribution, and evaluation gate

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_name", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=20), server_default="discovered", nullable=False),
        sa.Column("source_path", sa.Text(), server_default="", nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('discovered', 'proposal', 'rollback')",
            name="ck_skill_version_source_kind",
        ),
        sa.ForeignKeyConstraint(["parent_version_id"], ["skill_versions.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["skill_proposals.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_name", "version", name="uq_skill_version_number"),
        sa.UniqueConstraint("user_id", "skill_name", "content_sha256", name="uq_skill_version_content"),
    )
    op.create_index("idx_skill_versions_active", "skill_versions", ["user_id", "skill_name", "is_active"])
    op.create_index(
        "uq_skill_versions_one_active",
        "skill_versions",
        ["user_id", "skill_name"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_skill_versions_user_id", "skill_versions", ["user_id"])
    op.create_index("ix_skill_versions_proposal_id", "skill_versions", ["proposal_id"])

    op.add_column("skill_proposals", sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("skill_proposals", sa.Column("applied_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "skill_proposals",
        sa.Column("eval_cases", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
    )
    op.add_column(
        "skill_proposals",
        sa.Column("gate_status", sa.String(length=20), server_default="pending", nullable=False),
    )
    op.add_column(
        "skill_proposals",
        sa.Column("gate_reason", sa.Text(), server_default="", nullable=False),
    )
    op.create_foreign_key(
        "fk_skill_proposals_base_version",
        "skill_proposals", "skill_versions", ["base_version_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_skill_proposals_applied_version",
        "skill_proposals", "skill_versions", ["applied_version_id"], ["id"],
    )
    op.create_check_constraint(
        "ck_skill_proposal_gate_status",
        "skill_proposals",
        "gate_status IN ('pending', 'running', 'passed', 'failed', 'error')",
    )

    op.create_table(
        "skill_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=32), server_default="", nullable=False),
        sa.Column("turn_id", sa.String(length=32), server_default="", nullable=False),
        sa.Column("skill_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_name", sa.String(length=80), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=20), server_default="uncertain", nullable=False),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("verification_status", sa.String(length=40), server_default="", nullable=False),
        sa.Column("run_status", sa.String(length=20), server_default="", nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'uncertain', 'cancelled')",
            name="ck_skill_execution_outcome",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_versions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_skill_executions_version", "skill_executions", ["skill_version_id", "created_at"])
    op.create_index("idx_skill_executions_user_skill", "skill_executions", ["user_id", "skill_name", "created_at"])
    op.create_index("ix_skill_executions_user_id", "skill_executions", ["user_id"])

    op.create_table(
        "skill_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluator_model", sa.String(length=255), server_default="", nullable=False),
        sa.Column("gate_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("gate_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("baseline_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("candidate_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("score_delta", sa.Float(), server_default="0", nullable=False),
        sa.Column("semantic_preservation", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("safety_pass", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("regressions", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("case_results", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("deterministic_checks", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "gate_status IN ('pending', 'running', 'passed', 'failed', 'error')",
            name="ck_skill_evaluation_gate_status",
        ),
        sa.ForeignKeyConstraint(["proposal_id"], ["skill_proposals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", name="uq_skill_evaluation_proposal"),
    )
    op.create_index("idx_skill_evaluations_user_status", "skill_evaluations", ["user_id", "gate_status"])
    op.create_index("ix_skill_evaluations_user_id", "skill_evaluations", ["user_id"])


def downgrade() -> None:
    op.drop_table("skill_evaluations")
    op.drop_table("skill_executions")
    op.drop_constraint("ck_skill_proposal_gate_status", "skill_proposals", type_="check")
    op.drop_constraint("fk_skill_proposals_applied_version", "skill_proposals", type_="foreignkey")
    op.drop_constraint("fk_skill_proposals_base_version", "skill_proposals", type_="foreignkey")
    op.drop_column("skill_proposals", "gate_reason")
    op.drop_column("skill_proposals", "gate_status")
    op.drop_column("skill_proposals", "eval_cases")
    op.drop_column("skill_proposals", "applied_version_id")
    op.drop_column("skill_proposals", "base_version_id")
    op.drop_table("skill_versions")
