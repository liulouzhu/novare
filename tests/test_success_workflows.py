from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from novare.evolution.aggregator import build_success_workflow_candidates
from novare.evolution.skill_proposals import SkillFileManager, SkillProposalEvaluator
from novare.evolution.success_workflows import (
    SuccessfulWorkflowExtractor,
    build_successful_workflow_trigger,
)
from novare.llm_client import LLMResponse
from novare.recovery.state import RecoveryState, RunStatus, ToolCallStatus
from web.backend.db.models import SessionModel, SuccessfulWorkflowObservationModel, User
from web.backend.repositories.success_workflow_repo import SuccessfulWorkflowRepository


def _successful_state(tool_count: int = 5) -> RecoveryState:
    state = RecoveryState()
    state.register_tool_calls_batch([
        {
            "id": f"tc-{index}",
            "name": f"tool-{index % 3}",
            "arguments": {"query": f"secret-{index}", "api_key": "fake-secret-value"},
        }
        for index in range(tool_count)
    ])
    for record in state.tool_calls.values():
        record.status = ToolCallStatus.COMPLETED
        record.attempts = 1
    state.iteration = 5
    state.assistant_message_committed = True
    state.set_run_status(RunStatus.COMPLETED)
    return state


def test_complex_success_emits_sanitized_trigger():
    trigger = build_successful_workflow_trigger(
        recovery_state=_successful_state(),
        session_id="s1",
        user_goal="run a careful literature workflow",
        verification={"status": "verified"},
    )
    assert trigger is not None
    payload = trigger.to_dict()
    assert payload["metrics"]["tool_call_count"] == 5
    assert payload["confidence"] == pytest.approx(0.92)
    assert payload["tool_sequence"][0]["argument_names"] == ["api_key", "query"]
    assert "secret-0" not in json.dumps(payload["tool_sequence"])


def test_simple_or_failed_task_does_not_trigger():
    simple = _successful_state(tool_count=1)
    simple.iteration = 1
    assert build_successful_workflow_trigger(
        recovery_state=simple,
        session_id="s1",
        user_goal="simple",
        verification=None,
    ) is None
    failed = _successful_state()
    failed.set_run_status(RunStatus.FAILED)
    assert build_successful_workflow_trigger(
        recovery_state=failed,
        session_id="s1",
        user_goal="failed",
        verification={"status": "verified"},
    ) is None


@pytest.mark.asyncio
async def test_extractor_generalizes_workflow_without_arguments():
    class FakeLLM:
        async def collect_stream(self, messages, **kwargs):
            return LLMResponse(
                content=json.dumps({
                    "workflow_family": "literature evidence synthesis",
                    "workflow_name": "Evidence-first literature synthesis",
                    "summary": "Search, validate, synthesize.",
                    "when_to_use": "Complex literature questions",
                    "prerequisites": ["clear question"],
                    "steps": [
                        {"action": "search multiple sources", "tool_hint": "search"},
                        {"action": "verify and synthesize", "tool_hint": "reader"},
                    ],
                    "decision_points": ["broaden query when coverage is low"],
                    "pitfalls": ["single-source conclusions"],
                    "verification_steps": ["check every claim"],
                    "existing_skill_match": None,
                    "reusability": 0.9,
                    "confidence": 0.85,
                }),
                tool_calls=[], stop_reason="stop", usage={},
            )

    trigger = build_successful_workflow_trigger(
        recovery_state=_successful_state(),
        session_id="s1",
        user_goal="private query",
        verification={"status": "verified"},
    ).to_dict()
    extracted = await SuccessfulWorkflowExtractor(FakeLLM()).extract(
        trigger, skill_catalog=[],
    )
    assert len(extracted["workflow_key"]) == 64
    assert len(extracted["steps"]) == 2
    assert extracted["reusability"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_success_repository_is_idempotent_and_never_stores_goal(db_session):
    user = User(
        id=uuid.uuid4(), username=f"success_{uuid.uuid4().hex[:8]}",
        email=f"success_{uuid.uuid4().hex[:8]}@test.local", password_hash="x",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(SessionModel(id="success-session", user_id=user.id, title="success"))
    await db_session.flush()
    trigger = {
        "session_id": "success-session", "run_id": "run-1", "turn_id": "turn-1",
        "task_signature": "a" * 64, "user_goal": "api_key=fake-do-not-store",
        "tool_sequence": [{
            "tool": "search", "argument_names": ["api_key", "query"],
            "status": "completed", "attempts": 1,
        }],
        "verification_status": "verified", "complexity_score": 0.9,
        "metrics": {"tool_call_count": 5},
    }
    extracted = {
        "workflow_key": "b" * 64, "workflow_family": "research synthesis",
        "workflow_name": "Research synthesis", "summary": "safe summary",
        "when_to_use": "complex research", "prerequisites": [],
        "steps": [{"action": "search", "tool_hint": "search"}, {"action": "verify", "tool_hint": "reader"}],
        "decision_points": [], "pitfalls": [], "verification_steps": ["verify"],
        "existing_skill_match": None, "reusability": 0.9, "confidence": 0.9,
    }
    repo = SuccessfulWorkflowRepository(db_session, user.id)
    first = await repo.upsert_observation(trigger, extracted)
    second = await repo.upsert_observation(trigger, extracted)
    assert first.id == second.id
    count = await db_session.scalar(select(func.count(SuccessfulWorkflowObservationModel.id)))
    assert count == 1
    serialized = json.dumps(_model_payload(first), ensure_ascii=False)
    assert "fake-do-not-store" not in serialized


def _model_payload(item) -> dict:
    return {
        "summary": item.summary,
        "when_to_use": item.when_to_use,
        "steps": item.steps,
        "tool_sequence": item.tool_sequence,
        "metrics": item.metrics,
    }


def test_success_candidates_require_three_independent_sessions():
    base = {
        "workflow_key": "c" * 64, "workflow_name": "workflow",
        "workflow_family": "family", "summary": "summary", "steps": [],
        "verification_steps": [], "eligible_for_learning": True,
        "confidence": 0.9, "reusability": 0.9,
    }
    observations = [{**base, "session_id": f"s{i}", "run_id": f"r{i}"} for i in range(3)]
    [candidate] = build_success_workflow_candidates(observations)
    assert candidate["support_status"] == "supported"
    assert candidate["suggested_proposal_type"] == "create"


@pytest.mark.asyncio
async def test_create_skill_can_be_evaluated_applied_and_rolled_back(tmp_path):
    root = tmp_path / "skills"
    manager = SkillFileManager(
        user_skill_root=root,
        source_roots=[],
        backup_root=tmp_path / "backups",
        max_bytes=10_000,
    )
    location = manager.locate_for_create("new-workflow")
    content = """---
name: new-workflow
description: reusable workflow
---
Follow the steps and verify the result.
"""

    class EvalLLM:
        async def collect_stream(self, messages, **kwargs):
            return LLMResponse(
                content=json.dumps({
                    "semantic_preservation": True, "safety_pass": True,
                    "regressions": [], "case_results": [{
                        "name": "case", "baseline_score": 0.0,
                        "candidate_score": 0.9, "reason": "new capability",
                    }],
                }),
                tool_calls=[], stop_reason="stop", usage={},
            )

    evaluation = await SkillProposalEvaluator(
        EvalLLM(), max_bytes=10_000, max_tokens=1000, min_delta=0.05,
    ).evaluate(
        skill_name="new-workflow", baseline_content="", candidate_content=content,
        eval_cases=[{"name": "case", "input": "x", "expected_behavior": "works"}],
        proposal_type="create",
    )
    assert evaluation.gate_status == "passed"
    backup = manager.create_backup(
        proposal_id="p1", source_path=location.source_path,
        target_path=location.target_path, expected_hash=location.base_content_sha256,
    )
    applied_hash = manager.apply(
        target_path=location.target_path, skill_name="new-workflow", proposed_content=content,
    )
    assert location.target_path.exists()
    manager.rollback(target_path=location.target_path, backup=backup, applied_hash=applied_hash)
    assert not location.target_path.exists()
