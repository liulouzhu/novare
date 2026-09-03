"""Skill diff proposal, approval support, backup, and rollback tests."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from novare.evolution.skill_proposals import (
    GeneratedSkillProposal,
    SkillFileManager,
    SkillProposalError,
    SkillProposalEvaluator,
    SkillProposalGenerator,
    StaleSkillProposalError,
    validate_skill_content,
)
from novare.llm_client import LLMResponse
from web.backend.db.models import (
    SkillProposalAuditModel,
    SkillProposalBackupModel,
    SkillProposalModel,
    User,
)
from web.backend.repositories.skill_proposal_repo import SkillProposalRepository
from web.backend.routes.evolution import _auto_apply_if_allowed, _proposal_out


BASE_SKILL = """---
name: demo-skill
description: demo
---

Always inspect the input before acting.
"""

PROPOSED_SKILL = """---
name: demo-skill
description: demo
---

Always inspect the input before acting.
When a query is too narrow, broaden it once and record the reason.
"""


def _manager(tmp_path: Path, *, with_user_override: bool = False):
    global_root = tmp_path / "global-skills"
    global_file = global_root / "demo-skill" / "SKILL.md"
    global_file.parent.mkdir(parents=True)
    global_file.write_text(BASE_SKILL, encoding="utf-8")
    user_root = tmp_path / "user" / ".novare" / "skills"
    if with_user_override:
        user_file = user_root / "demo-skill" / "SKILL.md"
        user_file.parent.mkdir(parents=True)
        user_file.write_text(BASE_SKILL + "User override.\n", encoding="utf-8")
    manager = SkillFileManager(
        user_skill_root=user_root,
        source_roots=[global_root],
        backup_root=tmp_path / "user" / ".novare" / "evolution" / "backups",
        max_bytes=15_360,
    )
    return manager, global_file, user_root / "demo-skill" / "SKILL.md"


@pytest.mark.asyncio
async def test_reviewer_generates_canonical_diff_without_writing_skill(tmp_path):
    manager, global_file, target_file = _manager(tmp_path)
    location = manager.locate("demo-skill")
    reviewer = AsyncMock()
    reviewer.collect_stream = AsyncMock(return_value=LLMResponse(
        content=json.dumps({
            "summary": "broaden narrow queries",
            "rationale": "three independent helpful observations",
            "risk_level": "low",
            "test_plan": ["narrow query recovers", "normal query unchanged"],
            "eval_cases": [{
                "name": "narrow query",
                "input": "Find evidence for a very narrow query",
                "expected_behavior": "Broaden once and record the reason",
            }],
            "proposed_content": PROPOSED_SKILL,
        }),
        tool_calls=[],
        stop_reason="stop",
        usage={},
    ))
    generator = SkillProposalGenerator(reviewer, max_bytes=15_360, max_tokens=4_000)

    proposal = await generator.generate(
        location=location,
        candidate_report={"support_status": "supported", "independent_sessions": 3},
        experiences=[{
            "trigger": "repeated_failure",
            "generalized_lesson": "broaden narrow query",
            "resolution_status": "helpful",
            "resolution_confidence": 0.8,
        }],
    )

    assert "--- a/demo-skill/SKILL.md" in proposal.unified_diff
    assert "+When a query is too narrow" in proposal.unified_diff
    assert proposal.eval_cases[0]["name"] == "narrow query"
    assert global_file.read_text(encoding="utf-8") == BASE_SKILL
    assert not target_file.exists()


def test_apply_creates_user_override_and_rollback_removes_it(tmp_path):
    manager, global_file, target_file = _manager(tmp_path)
    location = manager.locate("demo-skill")
    backup = manager.create_backup(
        proposal_id="proposal-1",
        source_path=location.source_path,
        target_path=location.target_path,
        expected_hash=location.base_content_sha256,
    )
    assert backup.target_existed is False
    applied_hash = manager.apply(
        target_path=location.target_path,
        skill_name=location.skill_name,
        proposed_content=PROPOSED_SKILL,
    )
    assert target_file.exists()

    manager.rollback(
        target_path=target_file,
        backup=backup,
        applied_hash=applied_hash,
    )

    assert not target_file.exists()
    assert global_file.read_text(encoding="utf-8") == BASE_SKILL
    assert backup.backup_path.read_text(encoding="utf-8") == BASE_SKILL


def test_rollback_restores_existing_user_override(tmp_path):
    manager, _global_file, target_file = _manager(tmp_path, with_user_override=True)
    original = target_file.read_text(encoding="utf-8")
    location = manager.locate("demo-skill")
    backup = manager.create_backup(
        proposal_id="proposal-2",
        source_path=location.source_path,
        target_path=location.target_path,
        expected_hash=location.base_content_sha256,
    )
    applied_hash = manager.apply(
        target_path=location.target_path,
        skill_name=location.skill_name,
        proposed_content=PROPOSED_SKILL,
    )
    manager.rollback(target_path=target_file, backup=backup, applied_hash=applied_hash)
    assert target_file.read_text(encoding="utf-8") == original


def test_stale_source_is_rejected_before_backup_or_apply(tmp_path):
    manager, global_file, _target_file = _manager(tmp_path)
    location = manager.locate("demo-skill")
    global_file.write_text(BASE_SKILL + "external edit\n", encoding="utf-8")

    with pytest.raises(StaleSkillProposalError):
        manager.create_backup(
            proposal_id="proposal-stale",
            source_path=location.source_path,
            target_path=location.target_path,
            expected_hash=location.base_content_sha256,
        )


def test_skill_name_and_size_guards():
    changed_name = PROPOSED_SKILL.replace("name: demo-skill", "name: other-skill")
    with pytest.raises(SkillProposalError, match="不得修改 Skill 名称"):
        validate_skill_content(changed_name, skill_name="demo-skill", max_bytes=15_360)
    with pytest.raises(SkillProposalError, match="超过"):
        validate_skill_content("x" * 2000, skill_name="demo-skill", max_bytes=1024)


@pytest.mark.asyncio
async def test_repository_records_state_transitions_backup_and_audit(db_session):
    user = User(
        id=uuid.uuid4(),
        username=f"proposal_{uuid.uuid4().hex[:8]}",
        email=f"proposal_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="not-used",
    )
    db_session.add(user)
    await db_session.flush()
    repo = SkillProposalRepository(db_session, user.id)
    proposal = await repo.create_generating(
        lesson_key="a" * 64,
        skill_name="demo-skill",
        source_path="/safe/source/SKILL.md",
        target_path="/safe/user/demo-skill/SKILL.md",
        base_content_sha256="b" * 64,
        candidate_snapshot={"support_status": "supported"},
        generated_by_model="reviewer-test",
        write_approval_required=True,
    )
    generated = GeneratedSkillProposal(
        proposed_content=PROPOSED_SKILL,
        unified_diff="--- a\n+++ b\n",
        summary="summary",
        rationale="rationale",
        risk_level="low",
        test_plan=["test"],
        eval_cases=[{
            "name": "narrow query",
            "input": "narrow evidence query",
            "expected_behavior": "broaden once",
        }],
    )
    await repo.complete_generation(proposal, generated)
    await repo.approve(proposal, "approved by user")
    await repo.create_backup(
        proposal,
        target_existed=False,
        content=BASE_SKILL,
        content_sha256="c" * 64,
        backup_path="/backup/SKILL.md",
    )
    await db_session.flush()

    assert proposal.status == "approved"
    assert proposal.write_approval_required is True
    assert _proposal_out(proposal)["requires_explicit_approval"] is True
    latest = await repo.get_latest_for_candidate(
        candidate_type="reflection",
        candidate_key="a" * 64,
    )
    assert latest.id == proposal.id
    assert await db_session.scalar(select(func.count(SkillProposalModel.id))) == 1
    assert await db_session.scalar(select(func.count(SkillProposalBackupModel.id))) == 1
    assert await db_session.scalar(select(func.count(SkillProposalAuditModel.id))) == 4
    actions = [item.action for item in await repo.list_audits(proposal.id)]
    assert actions == [
        "generation_started",
        "generation_completed",
        "approved",
        "backup_created",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_required", "expected_status", "target_exists"),
    [(False, "applied", True), (True, "draft", False)],
)
async def test_write_policy_auto_applies_or_waits_for_approval(
    db_session,
    tmp_path,
    approval_required,
    expected_status,
    target_exists,
):
    user = User(
        id=uuid.uuid4(),
        username=f"write_policy_{uuid.uuid4().hex[:8]}",
        email=f"write_policy_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="not-used",
    )
    db_session.add(user)
    await db_session.flush()
    manager, _global_file, target_file = _manager(tmp_path)
    location = manager.locate("demo-skill")
    repo = SkillProposalRepository(db_session, user.id)
    proposal = await repo.create_generating(
        lesson_key="d" * 64,
        skill_name=location.skill_name,
        source_path=str(location.source_path),
        target_path=str(location.target_path),
        base_content_sha256=location.base_content_sha256,
        candidate_snapshot={"support_status": "supported"},
        generated_by_model="reviewer-test",
        write_approval_required=approval_required,
    )
    await repo.complete_generation(
        proposal,
        GeneratedSkillProposal(
            proposed_content=PROPOSED_SKILL,
            unified_diff="--- a\n+++ b\n",
            summary="summary",
            rationale="rationale",
            risk_level="low",
            test_plan=["test"],
            eval_cases=[{
                "name": "narrow query",
                "input": "narrow evidence query",
                "expected_behavior": "broaden once",
            }],
        ),
    )
    proposal.gate_status = "passed"
    await db_session.commit()

    await _auto_apply_if_allowed(
        proposal=proposal,
        proposal_repo=repo,
        manager=manager,
        user_id=user.id,
        db=db_session,
    )

    assert proposal.status == expected_status
    assert target_file.exists() is target_exists
    output = _proposal_out(proposal)
    assert output["requires_explicit_approval"] is approval_required
    assert output["auto_apply"] is (not approval_required)
    if target_exists:
        assert target_file.read_text(encoding="utf-8") == PROPOSED_SKILL
        assert await repo.get_backup(proposal.id) is not None


@pytest.mark.asyncio
async def test_automatic_evaluation_gate_passes_only_on_measured_improvement():
    evaluator_llm = AsyncMock()
    evaluator_llm.collect_stream = AsyncMock(return_value=LLMResponse(
        content=json.dumps({
            "semantic_preservation": True,
            "safety_pass": True,
            "regressions": [],
            "case_results": [{
                "name": "narrow query",
                "baseline_score": 0.45,
                "candidate_score": 0.85,
                "reason": "candidate adds a bounded recovery step",
            }],
        }),
        tool_calls=[],
        stop_reason="stop",
        usage={},
    ))
    evaluator = SkillProposalEvaluator(
        evaluator_llm,
        max_bytes=15_360,
        max_tokens=3_000,
        min_delta=0.05,
    )
    result = await evaluator.evaluate(
        skill_name="demo-skill",
        baseline_content=BASE_SKILL,
        candidate_content=PROPOSED_SKILL,
        eval_cases=[{
            "name": "narrow query",
            "input": "narrow evidence query",
            "expected_behavior": "broaden once",
        }],
    )

    assert result.gate_status == "passed"
    assert result.score_delta == 0.4
    assert result.semantic_preservation is True
    assert result.safety_pass is True


@pytest.mark.asyncio
async def test_automatic_evaluation_gate_rejects_regression():
    evaluator_llm = AsyncMock()
    evaluator_llm.collect_stream = AsyncMock(return_value=LLMResponse(
        content=json.dumps({
            "semantic_preservation": True,
            "safety_pass": True,
            "regressions": [],
            "case_results": [{
                "name": "normal query",
                "baseline_score": 0.9,
                "candidate_score": 0.7,
                "reason": "candidate adds unnecessary behavior",
            }],
        }),
        tool_calls=[],
        stop_reason="stop",
        usage={},
    ))
    evaluator = SkillProposalEvaluator(
        evaluator_llm,
        max_bytes=15_360,
        max_tokens=3_000,
        min_delta=0.05,
    )
    result = await evaluator.evaluate(
        skill_name="demo-skill",
        baseline_content=BASE_SKILL,
        candidate_content=PROPOSED_SKILL,
        eval_cases=[{
            "name": "normal query",
            "input": "ordinary evidence query",
            "expected_behavior": "retain original behavior",
        }],
    )

    assert result.gate_status == "failed"
    assert "normal query" in result.regressions


@pytest.mark.asyncio
async def test_automatic_evaluation_gate_fails_closed_without_cases():
    evaluator_llm = AsyncMock()
    evaluator = SkillProposalEvaluator(
        evaluator_llm,
        max_bytes=15_360,
        max_tokens=3_000,
        min_delta=0.05,
    )
    result = await evaluator.evaluate(
        skill_name="demo-skill",
        baseline_content=BASE_SKILL,
        candidate_content=PROPOSED_SKILL,
        eval_cases=[],
    )

    assert result.gate_status == "failed"
    assert "has_eval_cases" in result.gate_reason
    evaluator_llm.collect_stream.assert_not_awaited()
