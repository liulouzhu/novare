"""Persistence for reviewer-generated Skill proposals and audit events."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novare.recovery.classifier import sanitize_error
from web.backend.db.models import (
    SkillEvaluationModel,
    SkillProposalAuditModel,
    SkillProposalBackupModel,
    SkillProposalModel,
)

from .base import BaseRepository


class SkillProposalRepository(BaseRepository):
    def __init__(self, db: AsyncSession, user_id: UUID):
        super().__init__(db, user_id)

    async def create_generating(
        self,
        *,
        lesson_key: str,
        skill_name: str,
        source_path: str,
        target_path: str,
        base_content_sha256: str,
        candidate_snapshot: dict,
        generated_by_model: str,
        base_version_id: UUID | None = None,
        candidate_type: str = "reflection",
        proposal_type: str = "patch",
        write_approval_required: bool = False,
    ) -> SkillProposalModel:
        proposal = SkillProposalModel(
            user_id=self.user_id,
            lesson_key=lesson_key[:64],
            candidate_type=(
                "successful_workflow" if candidate_type == "successful_workflow"
                else "reflection"
            ),
            proposal_type="create" if proposal_type == "create" else "patch",
            write_approval_required=bool(write_approval_required),
            skill_name=skill_name[:80],
            source_path=source_path,
            target_path=target_path,
            base_content_sha256=base_content_sha256[:64],
            base_version_id=base_version_id,
            candidate_snapshot=candidate_snapshot,
            generated_by_model=generated_by_model[:255],
            status="generating",
        )
        self.db.add(proposal)
        await self.db.flush()
        await self.add_audit(
            proposal.id,
            action="generation_started",
            from_status="",
            to_status="generating",
            details={
                "lesson_key": lesson_key[:64],
                "skill_name": skill_name[:80],
                "candidate_type": candidate_type,
                "proposal_type": proposal_type,
                "write_approval_required": bool(write_approval_required),
            },
        )
        return proposal

    async def get_latest_for_candidate(
        self, *, candidate_type: str, candidate_key: str,
    ) -> SkillProposalModel | None:
        result = await self.db.execute(
            select(SkillProposalModel)
            .where(
                SkillProposalModel.user_id == self.user_id,
                SkillProposalModel.candidate_type == candidate_type,
                SkillProposalModel.lesson_key == candidate_key,
            )
            .order_by(SkillProposalModel.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get(
        self, proposal_id: UUID, *, for_update: bool = False,
    ) -> SkillProposalModel | None:
        query = select(SkillProposalModel).where(
            SkillProposalModel.id == proposal_id,
            SkillProposalModel.user_id == self.user_id,
        )
        if for_update:
            query = query.with_for_update()
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 100) -> list[SkillProposalModel]:
        result = await self.db.execute(
            select(SkillProposalModel)
            .where(SkillProposalModel.user_id == self.user_id)
            .order_by(SkillProposalModel.created_at.desc())
            .limit(max(1, min(500, limit)))
        )
        return list(result.scalars().all())

    async def complete_generation(self, proposal: SkillProposalModel, generated) -> None:
        previous = proposal.status
        proposal.proposed_content = generated.proposed_content
        proposal.unified_diff = generated.unified_diff
        proposal.summary = generated.summary
        proposal.rationale = generated.rationale
        proposal.risk_level = generated.risk_level
        proposal.test_plan = generated.test_plan
        proposal.eval_cases = generated.eval_cases
        proposal.generation_error = ""
        proposal.status = "draft"
        await self.add_audit(
            proposal.id,
            action="generation_completed",
            from_status=previous,
            to_status="draft",
            details={
                "risk_level": generated.risk_level,
                "diff_sha256": _text_hash(generated.unified_diff),
            },
        )
        await self.db.flush()

    async def start_evaluation(self, proposal: SkillProposalModel) -> None:
        previous = proposal.gate_status
        proposal.gate_status = "running"
        proposal.gate_reason = ""
        await self.add_audit(
            proposal.id,
            action="evaluation_started",
            from_status=previous,
            to_status="running",
        )
        await self.db.flush()

    async def save_evaluation(
        self,
        proposal: SkillProposalModel,
        result,
        *,
        evaluator_model: str,
    ) -> SkillEvaluationModel:
        evaluation = await self.get_evaluation(proposal.id)
        values = {
            "evaluator_model": evaluator_model[:255],
            "gate_status": result.gate_status,
            "gate_reason": result.gate_reason[:1000],
            "baseline_score": result.baseline_score,
            "candidate_score": result.candidate_score,
            "score_delta": result.score_delta,
            "semantic_preservation": result.semantic_preservation,
            "safety_pass": result.safety_pass,
            "regressions": result.regressions,
            "case_results": result.case_results,
            "deterministic_checks": result.deterministic_checks,
            "evaluated_at": datetime.now(timezone.utc),
        }
        if evaluation is None:
            evaluation = SkillEvaluationModel(
                proposal_id=proposal.id,
                user_id=self.user_id,
                **values,
            )
            self.db.add(evaluation)
        else:
            for key, value in values.items():
                setattr(evaluation, key, value)
        previous = proposal.gate_status
        proposal.gate_status = result.gate_status
        proposal.gate_reason = result.gate_reason[:1000]
        await self.add_audit(
            proposal.id,
            action="evaluation_passed" if result.gate_status == "passed" else "evaluation_failed",
            from_status=previous,
            to_status=result.gate_status,
            details={
                "baseline_score": result.baseline_score,
                "candidate_score": result.candidate_score,
                "score_delta": result.score_delta,
                "reason": result.gate_reason,
            },
        )
        await self.db.flush()
        return evaluation

    async def fail_evaluation(
        self,
        proposal: SkillProposalModel,
        error: str,
        *,
        evaluator_model: str,
    ) -> SkillEvaluationModel:
        safe_error = sanitize_error(error)[:1000]
        evaluation = await self.get_evaluation(proposal.id)
        values = {
            "evaluator_model": evaluator_model[:255],
            "gate_status": "error",
            "gate_reason": safe_error,
            "evaluated_at": datetime.now(timezone.utc),
        }
        if evaluation is None:
            evaluation = SkillEvaluationModel(
                proposal_id=proposal.id,
                user_id=self.user_id,
                **values,
            )
            self.db.add(evaluation)
        else:
            for key, value in values.items():
                setattr(evaluation, key, value)
        previous = proposal.gate_status
        proposal.gate_status = "error"
        proposal.gate_reason = safe_error
        await self.add_audit(
            proposal.id,
            action="evaluation_error",
            from_status=previous,
            to_status="error",
            details={"error": safe_error},
        )
        await self.db.flush()
        return evaluation

    async def get_evaluation(self, proposal_id: UUID) -> SkillEvaluationModel | None:
        result = await self.db.execute(
            select(SkillEvaluationModel).where(
                SkillEvaluationModel.proposal_id == proposal_id,
                SkillEvaluationModel.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none()

    async def fail_generation(self, proposal: SkillProposalModel, error: str) -> None:
        previous = proposal.status
        proposal.status = "failed"
        proposal.generation_error = sanitize_error(error)[:1000]
        await self.add_audit(
            proposal.id,
            action="generation_failed",
            from_status=previous,
            to_status="failed",
            details={"error": proposal.generation_error},
        )
        await self.db.flush()

    async def approve(self, proposal: SkillProposalModel, comment: str = "") -> None:
        previous = proposal.status
        proposal.status = "approved"
        proposal.approval_comment = sanitize_error(comment)[:1000]
        proposal.approved_at = datetime.now(timezone.utc)
        await self.add_audit(
            proposal.id,
            action="approved",
            from_status=previous,
            to_status="approved",
            details={"comment": proposal.approval_comment},
        )
        await self.db.flush()

    async def reject(self, proposal: SkillProposalModel, comment: str = "") -> None:
        previous = proposal.status
        proposal.status = "rejected"
        proposal.approval_comment = sanitize_error(comment)[:1000]
        await self.add_audit(
            proposal.id,
            action="rejected",
            from_status=previous,
            to_status="rejected",
            details={"comment": proposal.approval_comment},
        )
        await self.db.flush()

    async def create_backup(
        self,
        proposal: SkillProposalModel,
        *,
        target_existed: bool,
        content: str,
        content_sha256: str,
        backup_path: str,
    ) -> SkillProposalBackupModel:
        existing = await self.get_backup(proposal.id)
        if existing is not None:
            return existing
        backup = SkillProposalBackupModel(
            proposal_id=proposal.id,
            user_id=self.user_id,
            target_existed=target_existed,
            content=content,
            content_sha256=content_sha256[:64],
            backup_path=backup_path,
        )
        self.db.add(backup)
        await self.add_audit(
            proposal.id,
            action="backup_created",
            from_status=proposal.status,
            to_status=proposal.status,
            details={
                "target_existed": target_existed,
                "content_sha256": content_sha256[:64],
            },
        )
        await self.db.flush()
        return backup

    async def get_backup(self, proposal_id: UUID) -> SkillProposalBackupModel | None:
        result = await self.db.execute(
            select(SkillProposalBackupModel).where(
                SkillProposalBackupModel.proposal_id == proposal_id,
                SkillProposalBackupModel.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_audits(self, proposal_id: UUID) -> list[SkillProposalAuditModel]:
        result = await self.db.execute(
            select(SkillProposalAuditModel)
            .where(
                SkillProposalAuditModel.proposal_id == proposal_id,
                SkillProposalAuditModel.user_id == self.user_id,
            )
            .order_by(SkillProposalAuditModel.id.asc())
        )
        return list(result.scalars().all())

    async def add_audit(
        self,
        proposal_id: UUID,
        *,
        action: str,
        from_status: str,
        to_status: str,
        details: dict | None = None,
    ) -> SkillProposalAuditModel:
        audit = SkillProposalAuditModel(
            proposal_id=proposal_id,
            user_id=self.user_id,
            action=action[:40],
            from_status=from_status[:20],
            to_status=to_status[:20],
            details=_safe_details(details or {}),
        )
        self.db.add(audit)
        await self.db.flush()
        return audit


def _safe_details(details: dict) -> dict:
    safe: dict = {}
    for key, value in list(details.items())[:30]:
        safe_key = str(key)[:80]
        if isinstance(value, str):
            safe[safe_key] = sanitize_error(value)[:1000]
        elif isinstance(value, (bool, int, float)) or value is None:
            safe[safe_key] = value
        else:
            safe[safe_key] = sanitize_error(str(value))[:1000]
    return safe


def _text_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
