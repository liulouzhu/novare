"""add explicit versus automatic Skill selection mode

Revision ID: f2a3b4c5d6e7
Revises: e0f1a2b3c4d5
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skill_executions",
        sa.Column(
            "selection_mode",
            sa.String(length=20),
            server_default="explicit",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_skill_execution_selection_mode",
        "skill_executions",
        "selection_mode IN ('explicit', 'automatic')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_skill_execution_selection_mode",
        "skill_executions",
        type_="check",
    )
    op.drop_column("skill_executions", "selection_mode")
