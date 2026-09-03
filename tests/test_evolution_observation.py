"""Observation-mode self-evolution tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from novare.evolution.aggregator import build_candidate_reports
from novare.evolution.resolver import ReflectionResolutionTracker
from novare.agent_loop import AgentLoop
from novare.llm_client import LLMResponse, ToolCall
from novare.recovery.policy import RetryPolicy
from novare.reflexion.triggers import ToolEventSummary, compute_action_fingerprint
from novare.reflexion.types import ReflectionRecord
from novare.session import Session
from novare.tools.registry import ToolDef, ToolRegistry
from web.backend.db.models import (
    EvolutionExperienceModel,
    ReflectionResolutionModel,
    SessionModel,
    User,
)
from web.backend.repositories.evolution_observation_repo import (
    EvolutionObservationRepository,
)


def _record(*, reflection_id: str = "r1") -> ReflectionRecord:
    suggested = {"tool": "paper_search", "arguments": {"query": "safe query"}}
    return ReflectionRecord(
        reflection_id=reflection_id,
        trigger="repeated_failure",
        trigger_fingerprint="trigger-fp",
        evidence_refs=["event:failed-1"],
        failure_type="bad_parameters",
        diagnosis="搜索参数过窄",
        preserve=["用户目标"],
        changes=["放宽查询词后再次检索"],
        forbidden_action_fingerprints=[compute_action_fingerprint("paper_search", {"query": "bad"})],
        revised_plan=["换用更宽泛的查询词"],
        suggested_next_action=suggested,
        decision="revise",
        validated=True,
        applied=True,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _event(*, ok: bool, query: str = "safe query", event_id: str = "next-1") -> ToolEventSummary:
    return ToolEventSummary(
        tool_call_id=event_id,
        tool_name="paper_search",
        action_fingerprint=compute_action_fingerprint("paper_search", {"query": query}),
        ok=ok,
        error_code=None if ok else "BAD_REQUEST",
        outcome="completed" if ok else "failed",
        summary="found results" if ok else "invalid query",
    )


def _tracker() -> ReflectionResolutionTracker:
    return ReflectionResolutionTracker(
        session_id="session-1",
        run_id="run-1",
        turn_id="turn-1",
        user_goal="查找关于测试时训练的论文",
    )


def test_successful_post_reflection_action_is_helpful():
    tracker = _tracker()
    tracker.register(_record(), iteration=0, failed_tool="paper_search", error_code="BAD_REQUEST")
    tracker.observe_batch(iteration=1, events=[_event(ok=True)], made_progress=True)

    [resolution] = tracker.finalize(run_status="completed", verification=None)

    assert resolution.status.value == "helpful"
    assert resolution.confidence == pytest.approx(0.70)
    assert resolution.suggested_action_succeeded is True
    assert resolution.to_dict()["applied"] is False


def test_trigger_batch_cannot_validate_its_own_reflection():
    tracker = _tracker()
    tracker.register(_record(), iteration=2)
    tracker.observe_batch(iteration=2, events=[_event(ok=True)], made_progress=True)

    [resolution] = tracker.finalize(run_status="completed", verification=None)

    assert resolution.status.value == "uncertain"
    assert resolution.evidence_event_ids == []


def test_failed_suggestion_and_failed_verification_are_harmful():
    tracker = _tracker()
    tracker.register(_record(), iteration=0)
    tracker.observe_batch(iteration=1, events=[_event(ok=False)], made_progress=False)

    [resolution] = tracker.finalize(
        run_status="completed",
        verification={"status": "repair_failed"},
    )

    assert resolution.status.value == "harmful"
    assert resolution.confidence == pytest.approx(0.80)


def test_candidate_requires_independent_sessions():
    base = {
        "lesson_key": "same-lesson",
        "resolution_status": "helpful",
        "resolution_confidence": 0.8,
        "failure_type": "bad_parameters",
        "failed_tool": "paper_search",
        "generalized_lesson": "放宽查询词",
    }
    repeated_one_session = [
        {**base, "session_id": "s1", "reflection_id": f"r{i}"} for i in range(5)
    ]
    [report] = build_candidate_reports(repeated_one_session, min_independent_sessions=3)
    assert report["support_status"] == "anecdotal"
    assert report["applied"] is False

    independent = [
        {**base, "session_id": f"s{i}", "reflection_id": f"i{i}"} for i in range(3)
    ]
    [report] = build_candidate_reports(independent, min_independent_sessions=3)
    assert report["support_status"] == "supported"


@pytest.mark.asyncio
async def test_agent_loop_emits_resolution_after_real_reflection():
    async def reader(arguments, workspace=None):
        if arguments["path"] == "/bad":
            return "Error: Invalid argument 'path'"
        return '{"ok": true, "summary": "paper loaded"}'

    registry = ToolRegistry()
    registry.register_tool(ToolDef(
        name="reader",
        description="read a paper",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=reader,
        idempotency="read",
        retry_policy=RetryPolicy(max_attempts=1),
    ))
    failed_fp = compute_action_fingerprint("reader", {"path": "/bad"})
    agent_responses = [
        LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc-bad", name="reader", arguments={"path": "/bad"})],
            stop_reason="tool_calls",
            usage={},
        ),
        LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc-good", name="reader", arguments={"path": "/good"})],
            stop_reason="tool_calls",
            usage={},
        ),
        LLMResponse(content="done", tool_calls=[], stop_reason="stop", usage={}),
    ]
    reflection_response = LLMResponse(
        content=json.dumps({
            "failure_type": "BAD_PARAMETERS",
            "evidence_refs": ["event:tc-bad"],
            "diagnosis": "path parameter is invalid",
            "preserve": ["user goal"],
            "changes": ["use a valid path"],
            "forbidden_repeat": [failed_fp],
            "revised_plan": ["read valid paper", "answer"],
            "suggested_next_action": {"tool": "reader", "arguments": {"path": "/good"}},
            "decision": "REPLAN",
        }),
        tool_calls=[],
        stop_reason="stop",
        usage={},
    )
    llm = AsyncMock()

    async def collect(messages, **kwargs):
        if str(messages[0].get("content", "")).startswith("你是一个严谨的反思分析器"):
            return reflection_response
        return agent_responses.pop(0)

    llm.collect_stream = AsyncMock(side_effect=collect)
    loop = AgentLoop(
        llm_client=llm,
        tool_registry=registry,
        system_prompt="test",
        reflexion_enabled=True,
        evolution_observe_enabled=True,
    )
    resolutions: list[dict] = []

    result = await loop.run_turn(
        Session(),
        "read the paper",
        on_reflection_resolution=lambda item: resolutions.append(item),
    )

    assert result == "done"
    assert len(resolutions) == 1
    assert resolutions[0]["status"] == "helpful"
    assert resolutions[0]["evidence_event_ids"] == ["tc-good"]
    assert resolutions[0]["applied"] is False


@pytest.mark.asyncio
async def test_repository_is_idempotent_and_redacts_secrets(db_session):
    test_user = User(
        id=uuid.uuid4(),
        username=f"evolution_{uuid.uuid4().hex[:8]}",
        email=f"evolution_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="not-used",
    )
    db_session.add(test_user)
    await db_session.flush()
    session = SessionModel(id="session-1", user_id=test_user.id, title="observation")
    db_session.add(session)
    await db_session.flush()
    repo = EvolutionObservationRepository(db_session, test_user.id)
    payload = {
        "reflection_id": "r-secret",
        "session_id": "session-1",
        "run_id": "run-1",
        "turn_id": "turn-1",
        "task_signature": "a" * 64,
        "trigger": "repeated_failure",
        "failure_type": "bad_parameters",
        "failed_tool": "paper_search",
        "error_code": "BAD_REQUEST",
        "diagnosis": "api_key=fake-super-secret should not be stored",
        "changes": ["retry without Bearer abcdefghijklmnop"],
        "revised_plan": ["broaden search"],
        "suggested_next_action": {
            "tool": "paper_search",
            "arguments": {"query": "x", "api_key": "fake-super-secret"},
        },
        "status": "helpful",
        "confidence": 0.8,
        "evidence_event_ids": ["next-1"],
        "summary": "successful",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }

    first_resolution, first_experience = await repo.upsert_observation(payload)
    second_resolution, second_experience = await repo.upsert_observation(payload)
    await db_session.flush()

    assert first_resolution.id == second_resolution.id
    assert first_experience.id == second_experience.id
    assert "super-secret" not in first_resolution.diagnosis
    assert first_resolution.suggested_next_action == {
        "tool": "paper_search",
        "argument_names": ["api_key", "query"],
    }
    resolution_count = await db_session.scalar(select(func.count(ReflectionResolutionModel.id)))
    experience_count = await db_session.scalar(select(func.count(EvolutionExperienceModel.id)))
    assert resolution_count == 1
    assert experience_count == 1
    assert first_experience.eligible_for_learning is True
