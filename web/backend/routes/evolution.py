"""Read-only endpoints for observation-mode self-evolution evidence."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from novare.evolution.aggregator import (
    build_candidate_reports,
    build_success_workflow_candidates,
)
from novare.evolution.skill_proposals import (
    SkillBackup,
    SkillFileManager,
    SkillProposalError,
    SkillProposalEvaluator,
    SkillProposalGenerator,
    StaleSkillProposalError,
)
from novare.config import get_user_workspace
from web.backend.auth.dependencies import get_current_user
from web.backend.db.base import get_db
from web.backend.db.models import User
from web.backend.repositories import (
    EvolutionObservationRepository,
    SkillProposalRepository,
    SkillVersionRepository,
    SuccessfulWorkflowRepository,
)


router = APIRouter(prefix="/api/evolution", tags=["evolution"])


def _configured_min_sessions() -> int:
    try:
        value = int(os.environ.get("NOVARE_EVOLUTION_MIN_INDEPENDENT_SESSIONS", "3"))
    except ValueError:
        value = 3
    return max(2, min(20, value))


DEFAULT_MIN_INDEPENDENT_SESSIONS = _configured_min_sessions()


class GenerateSkillProposalRequest(BaseModel):
    lesson_key: str | None = Field(None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    workflow_key: str | None = Field(None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    skill_name: str = Field(..., min_length=1, max_length=80)
    proposal_type: str = Field("patch", pattern=r"^(patch|create)$")


class ProposalDecisionRequest(BaseModel):
    comment: str = Field("", max_length=1000)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _resolution_out(item) -> dict:
    return {
        "id": str(item.id),
        "reflection_id": item.reflection_id,
        "session_id": item.session_id,
        "run_id": item.run_id,
        "turn_id": item.turn_id,
        "task_signature": item.task_signature,
        "trigger": item.trigger,
        "failure_type": item.failure_type,
        "failed_tool": item.failed_tool,
        "error_code": item.error_code,
        "diagnosis": item.diagnosis,
        "changes": item.changes or [],
        "revised_plan": item.revised_plan or [],
        "suggested_next_action": item.suggested_next_action,
        "status": item.status,
        "confidence": item.confidence,
        "signals": item.signals or {},
        "evidence_event_ids": item.evidence_event_ids or [],
        "summary": item.summary,
        "created_at": _iso(item.created_at),
        "resolved_at": _iso(item.resolved_at),
        "applied": False,
    }


def _experience_out(item) -> dict:
    return {
        "id": str(item.id),
        "reflection_id": item.reflection_id,
        "session_id": item.session_id,
        "run_id": item.run_id,
        "task_signature": item.task_signature,
        "lesson_key": item.lesson_key,
        "experience_type": item.experience_type,
        "trigger": item.trigger,
        "failure_type": item.failure_type,
        "failed_tool": item.failed_tool,
        "error_code": item.error_code,
        "generalized_lesson": item.generalized_lesson,
        "resolution_status": item.resolution_status,
        "resolution_confidence": item.resolution_confidence,
        "evidence_refs": item.evidence_refs or [],
        "model_name": item.model_name,
        "environment_fingerprint": item.environment_fingerprint,
        "eligible_for_learning": item.eligible_for_learning,
        "rejection_reason": item.rejection_reason,
        "created_at": _iso(item.created_at),
        "applied": False,
    }


def _success_observation_out(item) -> dict:
    return {
        "id": str(item.id),
        "session_id": item.session_id,
        "run_id": item.run_id,
        "turn_id": item.turn_id,
        "task_signature": item.task_signature,
        "workflow_key": item.workflow_key,
        "workflow_family": item.workflow_family,
        "workflow_name": item.workflow_name,
        "summary": item.summary,
        "when_to_use": item.when_to_use,
        "prerequisites": item.prerequisites or [],
        "steps": item.steps or [],
        "decision_points": item.decision_points or [],
        "pitfalls": item.pitfalls or [],
        "verification_steps": item.verification_steps or [],
        "tool_sequence": item.tool_sequence or [],
        "existing_skill_match": item.existing_skill_match,
        "reusability": item.reusability,
        "confidence": item.confidence,
        "complexity_score": item.complexity_score,
        "verification_status": item.verification_status,
        "metrics": item.metrics or {},
        "eligible_for_learning": item.eligible_for_learning,
        "rejection_reason": item.rejection_reason,
        "created_at": _iso(item.created_at),
        "applied": False,
    }


def _proposal_out(item, *, include_content: bool = True) -> dict:
    result = {
        "id": str(item.id),
        "lesson_key": item.lesson_key,
        "candidate_type": item.candidate_type,
        "proposal_type": item.proposal_type,
        "skill_name": item.skill_name,
        "base_content_sha256": item.base_content_sha256,
        "base_version_id": str(item.base_version_id) if item.base_version_id else None,
        "applied_version_id": str(item.applied_version_id) if item.applied_version_id else None,
        "summary": item.summary,
        "rationale": item.rationale,
        "risk_level": item.risk_level,
        "test_plan": item.test_plan or [],
        "eval_cases": item.eval_cases or [],
        "gate_status": item.gate_status,
        "gate_reason": item.gate_reason,
        "status": item.status,
        "generated_by_model": item.generated_by_model,
        "generation_error": item.generation_error,
        "approval_comment": item.approval_comment,
        "approved_at": _iso(item.approved_at),
        "applied_content_sha256": item.applied_content_sha256,
        "applied_at": _iso(item.applied_at),
        "rolled_back_at": _iso(item.rolled_back_at),
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
        "requires_explicit_approval": True,
        "auto_apply": False,
    }
    if include_content:
        result["unified_diff"] = item.unified_diff
        result["proposed_content"] = item.proposed_content
        result["candidate_snapshot"] = item.candidate_snapshot or {}
    return result


def _evaluation_out(item) -> dict | None:
    if item is None:
        return None
    return {
        "id": str(item.id),
        "proposal_id": str(item.proposal_id),
        "evaluator_model": item.evaluator_model,
        "gate_status": item.gate_status,
        "gate_reason": item.gate_reason,
        "baseline_score": item.baseline_score,
        "candidate_score": item.candidate_score,
        "score_delta": item.score_delta,
        "semantic_preservation": item.semantic_preservation,
        "safety_pass": item.safety_pass,
        "regressions": item.regressions or [],
        "case_results": item.case_results or [],
        "deterministic_checks": item.deterministic_checks or {},
        "evaluated_at": _iso(item.evaluated_at),
    }


def _version_out(item) -> dict:
    return {
        "id": str(item.id),
        "skill_name": item.skill_name,
        "version": item.version,
        "content_sha256": item.content_sha256,
        "source_kind": item.source_kind,
        "proposal_id": str(item.proposal_id) if item.proposal_id else None,
        "parent_version_id": str(item.parent_version_id) if item.parent_version_id else None,
        "is_active": item.is_active,
        "created_at": _iso(item.created_at),
    }


def _execution_out(item) -> dict:
    return {
        "id": str(item.id),
        "session_id": item.session_id,
        "run_id": item.run_id,
        "turn_id": item.turn_id,
        "skill_version_id": str(item.skill_version_id),
        "skill_name": item.skill_name,
        "content_sha256": item.content_sha256,
        "selection_mode": item.selection_mode,
        "outcome": item.outcome,
        "score": item.score,
        "verification_status": item.verification_status,
        "run_status": item.run_status,
        "metrics": item.metrics or {},
        "created_at": _iso(item.created_at),
    }


def _audit_out(item) -> dict:
    return {
        "id": item.id,
        "proposal_id": str(item.proposal_id),
        "action": item.action,
        "from_status": item.from_status,
        "to_status": item.to_status,
        "details": item.details or {},
        "created_at": _iso(item.created_at),
    }


def _proposal_runtime(user_id: UUID):
    # Delayed import avoids the app/router import cycle.
    from web.backend.app import agent_service

    config = agent_service.config
    if config is None:
        raise HTTPException(503, "Agent service is not ready")
    if not config.evolution_proposal_enabled:
        raise HTTPException(403, "Skill diff 提议模式未启用")
    workspace = Path(get_user_workspace(str(user_id))).resolve()
    manager = SkillFileManager(
        user_skill_root=workspace / ".novare" / "skills",
        source_roots=list(config.skill_dirs),
        backup_root=workspace / ".novare" / "evolution" / "backups",
        max_bytes=config.evolution_skill_max_bytes,
    )
    return agent_service, config, manager


async def _evaluate_proposal(
    *,
    proposal,
    proposal_repo: SkillProposalRepository,
    version_repo: SkillVersionRepository,
    agent_service,
    config,
    db: AsyncSession,
):
    baseline_version = (
        await version_repo.get(proposal.base_version_id)
        if proposal.base_version_id else None
    )
    if baseline_version is None and proposal.proposal_type != "create":
        raise SkillProposalError("提案缺少可追踪的基线 Skill 版本")
    await proposal_repo.start_evaluation(proposal)
    await db.commit()
    evaluator_model = config.model
    evaluator = SkillProposalEvaluator(
        agent_service.llm_client,
        max_bytes=config.evolution_skill_max_bytes,
        max_tokens=config.evolution_eval_max_tokens,
        min_delta=config.evolution_eval_min_delta,
    )
    try:
        result = await evaluator.evaluate(
            skill_name=proposal.skill_name,
            baseline_content=baseline_version.content if baseline_version else "",
            candidate_content=proposal.proposed_content,
            eval_cases=proposal.eval_cases or [],
            proposal_type=proposal.proposal_type,
        )
        evaluation = await proposal_repo.save_evaluation(
            proposal,
            result,
            evaluator_model=evaluator_model,
        )
        await db.commit()
        return evaluation
    except Exception as exc:
        await proposal_repo.fail_evaluation(
            proposal,
            str(exc) if isinstance(exc, SkillProposalError) else "自动评测失败",
            evaluator_model=evaluator_model,
        )
        await db.commit()
        return await proposal_repo.get_evaluation(proposal.id)


@router.get("/resolutions")
async def list_resolutions(
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = EvolutionObservationRepository(db, user.id)
    return [_resolution_out(item) for item in await repo.list_resolutions(limit=limit)]


@router.get("/experiences")
async def list_experiences(
    limit: int = Query(500, ge=1, le=5000),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = EvolutionObservationRepository(db, user.id)
    return [_experience_out(item) for item in await repo.list_experiences(limit=limit)]


@router.get("/candidates")
async def list_candidates(
    min_independent_sessions: int = Query(DEFAULT_MIN_INDEPENDENT_SESSIONS, ge=2, le=20),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = EvolutionObservationRepository(db, user.id)
    experiences = [
        _experience_out(item) for item in await repo.list_experiences(limit=5000)
    ]
    return build_candidate_reports(
        experiences,
        min_independent_sessions=min_independent_sessions,
    )


@router.get("/success-observations")
async def list_success_observations(
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = SuccessfulWorkflowRepository(db, user.id)
    return [
        _success_observation_out(item)
        for item in await repo.list_observations(limit=limit)
    ]


@router.get("/workflow-candidates")
async def list_workflow_candidates(
    min_independent_sessions: int = Query(DEFAULT_MIN_INDEPENDENT_SESSIONS, ge=2, le=20),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = SuccessfulWorkflowRepository(db, user.id)
    observations = [
        _success_observation_out(item)
        for item in await repo.list_observations(limit=5000)
    ]
    return build_success_workflow_candidates(
        observations,
        min_independent_sessions=min_independent_sessions,
    )


@router.get("/summary")
async def observation_summary(
    min_independent_sessions: int = Query(DEFAULT_MIN_INDEPENDENT_SESSIONS, ge=2, le=20),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = EvolutionObservationRepository(db, user.id)
    resolutions = await repo.list_resolutions(limit=500)
    experiences = [
        _experience_out(item) for item in await repo.list_experiences(limit=5000)
    ]
    candidates = build_candidate_reports(
        experiences,
        min_independent_sessions=min_independent_sessions,
    )
    success_repo = SuccessfulWorkflowRepository(db, user.id)
    success_observations = [
        _success_observation_out(item)
        for item in await success_repo.list_observations(limit=5000)
    ]
    workflow_candidates = build_success_workflow_candidates(
        success_observations,
        min_independent_sessions=min_independent_sessions,
    )
    status_counts: dict[str, int] = {}
    for item in resolutions:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1
    return {
        "mode": "observe_only",
        "skill_mutation_enabled": False,
        "resolution_count": len(resolutions),
        "experience_count": len(experiences),
        "candidate_count": len(candidates),
        "supported_candidate_count": sum(
            item["support_status"] == "supported" for item in candidates
        ),
        "successful_workflow_observation_count": len(success_observations),
        "workflow_candidate_count": len(workflow_candidates),
        "supported_workflow_candidate_count": sum(
            item["support_status"] == "supported" for item in workflow_candidates
        ),
        "resolution_status_counts": status_counts,
        "applied": False,
    }


@router.post("/proposals/generate")
async def generate_skill_proposal(
    body: GenerateSkillProposalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent_service, config, manager = _proposal_runtime(user.id)
    if agent_service.reviewer_llm is None:
        raise HTTPException(503, "未配置独立 reviewer 模型")

    if bool(body.lesson_key) == bool(body.workflow_key):
        raise HTTPException(422, "lesson_key 和 workflow_key 必须且只能提供一个")

    candidate_type = "reflection"
    candidate_key = body.lesson_key or body.workflow_key or ""
    if body.workflow_key:
        candidate_type = "successful_workflow"
        success_repo = SuccessfulWorkflowRepository(db, user.id)
        observation_models = await success_repo.get_by_workflow_key(body.workflow_key)
        if not observation_models:
            raise HTTPException(404, "未找到对应成功工作流候选")
        observations = [_success_observation_out(item) for item in observation_models]
        reports = build_success_workflow_candidates(
            observations,
            min_independent_sessions=config.evolution_min_independent_sessions,
        )
        candidate = next(
            (item for item in reports if item["workflow_key"] == body.workflow_key),
            None,
        )
        experiences = [
            {
                "trigger": "successful_workflow",
                "failure_type": "",
                "failed_tool": "",
                "error_code": "",
                "generalized_lesson": (
                    f"{item['summary']}; steps={json.dumps(item['steps'], ensure_ascii=False)}; "
                    f"verification={json.dumps(item['verification_steps'], ensure_ascii=False)}"
                ),
                "resolution_status": "helpful",
                "resolution_confidence": item["confidence"],
            }
            for item in observations
        ]
    else:
        observation_repo = EvolutionObservationRepository(db, user.id)
        experience_models = await observation_repo.get_experiences_by_lesson_key(
            body.lesson_key or "",
        )
        if not experience_models:
            raise HTTPException(404, "未找到对应候选经验")
        experiences = [_experience_out(item) for item in experience_models]
        reports = build_candidate_reports(
            experiences,
            min_independent_sessions=config.evolution_min_independent_sessions,
        )
        candidate = next(
            (item for item in reports if item["lesson_key"] == body.lesson_key),
            None,
        )
    if candidate is None or candidate["support_status"] != "supported":
        raise HTTPException(409, "候选尚未达到 supported，不能生成 Skill diff")

    try:
        location = (
            manager.locate_for_create(body.skill_name)
            if body.proposal_type == "create"
            else manager.locate(body.skill_name)
        )
    except SkillProposalError as exc:
        raise HTTPException(400, str(exc)) from exc

    proposal_repo = SkillProposalRepository(db, user.id)
    version_repo = SkillVersionRepository(db, user.id)
    base_version = None
    if body.proposal_type == "patch":
        base_version = await version_repo.ensure_version(
            skill_name=location.skill_name,
            content=location.current_content,
            source_kind="discovered",
            source_path=str(location.source_path),
            activate=True,
        )
    proposal = await proposal_repo.create_generating(
        lesson_key=candidate_key,
        skill_name=location.skill_name,
        source_path=str(location.source_path),
        target_path=str(location.target_path),
        base_content_sha256=location.base_content_sha256,
        candidate_snapshot=candidate,
        generated_by_model=config.reviewer_model or config.model,
        base_version_id=base_version.id if base_version else None,
        candidate_type=candidate_type,
        proposal_type=body.proposal_type,
    )
    await db.commit()

    generator = SkillProposalGenerator(
        agent_service.reviewer_llm,
        max_bytes=config.evolution_skill_max_bytes,
        max_tokens=config.evolution_proposal_max_tokens,
    )
    try:
        generated = await generator.generate(
            location=location,
            candidate_report=candidate,
            experiences=experiences,
            proposal_type=body.proposal_type,
        )
        await proposal_repo.complete_generation(proposal, generated)
        await db.commit()
        await _evaluate_proposal(
            proposal=proposal,
            proposal_repo=proposal_repo,
            version_repo=version_repo,
            agent_service=agent_service,
            config=config,
            db=db,
        )
        await db.refresh(proposal)
        return _proposal_out(proposal)
    except Exception as exc:
        safe_error = str(exc) if isinstance(exc, SkillProposalError) else "reviewer 生成失败"
        await proposal_repo.fail_generation(proposal, safe_error)
        await db.commit()
        raise HTTPException(
            502,
            {"proposal_id": str(proposal.id), "message": safe_error},
        ) from exc


@router.get("/proposals")
async def list_skill_proposals(
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = SkillProposalRepository(db, user.id)
    return [
        _proposal_out(item, include_content=False)
        for item in await repo.list(limit=limit)
    ]


@router.get("/proposals/{proposal_id}")
async def get_skill_proposal(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proposal = await SkillProposalRepository(db, user.id).get(proposal_id)
    if proposal is None:
        raise HTTPException(404, "Skill proposal not found")
    return _proposal_out(proposal)


@router.get("/proposals/{proposal_id}/evaluation")
async def get_skill_proposal_evaluation(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = SkillProposalRepository(db, user.id)
    if await repo.get(proposal_id) is None:
        raise HTTPException(404, "Skill proposal not found")
    return _evaluation_out(await repo.get_evaluation(proposal_id))


@router.post("/proposals/{proposal_id}/evaluate")
async def evaluate_skill_proposal(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent_service, config, _manager = _proposal_runtime(user.id)
    proposal_repo = SkillProposalRepository(db, user.id)
    proposal = await proposal_repo.get(proposal_id, for_update=True)
    if proposal is None:
        raise HTTPException(404, "Skill proposal not found")
    if proposal.status != "draft":
        raise HTTPException(409, f"只有 draft 提案可以评测，当前状态为 {proposal.status}")
    evaluation = await _evaluate_proposal(
        proposal=proposal,
        proposal_repo=proposal_repo,
        version_repo=SkillVersionRepository(db, user.id),
        agent_service=agent_service,
        config=config,
        db=db,
    )
    await db.refresh(proposal)
    return {"proposal": _proposal_out(proposal), "evaluation": _evaluation_out(evaluation)}


@router.get("/proposals/{proposal_id}/audit")
async def get_skill_proposal_audit(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = SkillProposalRepository(db, user.id)
    if await repo.get(proposal_id) is None:
        raise HTTPException(404, "Skill proposal not found")
    return [_audit_out(item) for item in await repo.list_audits(proposal_id)]


@router.post("/proposals/{proposal_id}/approve")
async def approve_skill_proposal(
    proposal_id: UUID,
    body: ProposalDecisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _proposal_runtime(user.id)
    repo = SkillProposalRepository(db, user.id)
    proposal = await repo.get(proposal_id, for_update=True)
    if proposal is None:
        raise HTTPException(404, "Skill proposal not found")
    if proposal.status != "draft":
        raise HTTPException(409, f"只有 draft 提案可以批准，当前状态为 {proposal.status}")
    if proposal.gate_status != "passed":
        raise HTTPException(409, f"自动评测门禁未通过：{proposal.gate_reason or proposal.gate_status}")
    await repo.approve(proposal, body.comment)
    await db.commit()
    await db.refresh(proposal)
    return _proposal_out(proposal)


@router.post("/proposals/{proposal_id}/reject")
async def reject_skill_proposal(
    proposal_id: UUID,
    body: ProposalDecisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _proposal_runtime(user.id)
    repo = SkillProposalRepository(db, user.id)
    proposal = await repo.get(proposal_id, for_update=True)
    if proposal is None:
        raise HTTPException(404, "Skill proposal not found")
    if proposal.status not in {"draft", "approved"}:
        raise HTTPException(409, f"当前状态 {proposal.status} 不能拒绝")
    await repo.reject(proposal, body.comment)
    await db.commit()
    await db.refresh(proposal)
    return _proposal_out(proposal)


@router.post("/proposals/{proposal_id}/apply")
async def apply_skill_proposal(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _agent_service, _config, manager = _proposal_runtime(user.id)
    repo = SkillProposalRepository(db, user.id)
    proposal = await repo.get(proposal_id, for_update=True)
    if proposal is None:
        raise HTTPException(404, "Skill proposal not found")
    if proposal.status != "approved":
        raise HTTPException(409, f"提案必须先批准，当前状态为 {proposal.status}")

    try:
        backup = manager.create_backup(
            proposal_id=str(proposal.id),
            source_path=Path(proposal.source_path),
            target_path=Path(proposal.target_path),
            expected_hash=proposal.base_content_sha256,
        )
        await repo.create_backup(
            proposal,
            target_existed=backup.target_existed,
            content=backup.content,
            content_sha256=backup.content_sha256,
            backup_path=str(backup.backup_path),
        )
        previous = proposal.status
        proposal.status = "applying"
        await repo.add_audit(
            proposal.id,
            action="apply_started",
            from_status=previous,
            to_status="applying",
        )
        await db.commit()

        applied_hash = manager.apply(
            target_path=Path(proposal.target_path),
            skill_name=proposal.skill_name,
            proposed_content=proposal.proposed_content,
        )
        applied_version = await SkillVersionRepository(db, user.id).ensure_version(
            skill_name=proposal.skill_name,
            content=proposal.proposed_content,
            source_kind="proposal",
            source_path=proposal.target_path,
            proposal_id=proposal.id,
            activate=True,
        )
        proposal.status = "applied"
        proposal.applied_content_sha256 = applied_hash
        proposal.applied_version_id = applied_version.id
        proposal.applied_at = datetime.now(timezone.utc)
        await repo.add_audit(
            proposal.id,
            action="applied",
            from_status="applying",
            to_status="applied",
            details={"applied_content_sha256": applied_hash},
        )
        await db.commit()
        await db.refresh(proposal)
        return _proposal_out(proposal)
    except StaleSkillProposalError as exc:
        previous = proposal.status
        proposal.status = "stale"
        await repo.add_audit(
            proposal.id,
            action="apply_rejected_stale",
            from_status=previous,
            to_status="stale",
            details={"error": str(exc)},
        )
        await db.commit()
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        previous = proposal.status
        proposal.status = "failed"
        await repo.add_audit(
            proposal.id,
            action="apply_failed",
            from_status=previous,
            to_status="failed",
            details={"error": str(exc)},
        )
        await db.commit()
        raise HTTPException(500, "Skill 应用失败，原文件备份已保留") from exc


@router.post("/proposals/{proposal_id}/rollback")
async def rollback_skill_proposal(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _agent_service, _config, manager = _proposal_runtime(user.id)
    repo = SkillProposalRepository(db, user.id)
    proposal = await repo.get(proposal_id, for_update=True)
    if proposal is None:
        raise HTTPException(404, "Skill proposal not found")
    if proposal.status != "applied":
        raise HTTPException(409, f"只有 applied 提案可以回滚，当前状态为 {proposal.status}")
    stored = await repo.get_backup(proposal.id)
    if stored is None:
        raise HTTPException(409, "找不到提案备份，拒绝回滚")
    backup = SkillBackup(
        target_existed=stored.target_existed,
        content=stored.content,
        content_sha256=stored.content_sha256,
        backup_path=Path(stored.backup_path),
    )
    try:
        manager.rollback(
            target_path=Path(proposal.target_path),
            backup=backup,
            applied_hash=proposal.applied_content_sha256,
        )
        version_repo = SkillVersionRepository(db, user.id)
        if stored.target_existed:
            await version_repo.ensure_version(
                skill_name=proposal.skill_name,
                content=stored.content,
                source_kind="rollback",
                source_path=proposal.target_path,
                proposal_id=proposal.id,
                activate=True,
            )
        else:
            await version_repo.deactivate_skill(proposal.skill_name)
        proposal.status = "rolled_back"
        proposal.rolled_back_at = datetime.now(timezone.utc)
        await repo.add_audit(
            proposal.id,
            action="rolled_back",
            from_status="applied",
            to_status="rolled_back",
            details={"restored_content_sha256": stored.content_sha256},
        )
        await db.commit()
        await db.refresh(proposal)
        return _proposal_out(proposal)
    except StaleSkillProposalError as exc:
        await repo.add_audit(
            proposal.id,
            action="rollback_rejected_stale",
            from_status="applied",
            to_status="applied",
            details={"error": str(exc)},
        )
        await db.commit()
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        await repo.add_audit(
            proposal.id,
            action="rollback_failed",
            from_status="applied",
            to_status="applied",
            details={"error": str(exc)},
        )
        await db.commit()
        raise HTTPException(500, "Skill 回滚失败，备份仍然保留") from exc


@router.get("/skills/{skill_name}/versions")
async def list_skill_versions(
    skill_name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    versions = await SkillVersionRepository(db, user.id).list_versions(skill_name)
    return [_version_out(item) for item in versions]


@router.get("/skills/{skill_name}/executions")
async def list_skill_executions(
    skill_name: str,
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    executions = await SkillVersionRepository(db, user.id).list_executions(
        skill_name=skill_name,
        limit=limit,
    )
    return [_execution_out(item) for item in executions]
