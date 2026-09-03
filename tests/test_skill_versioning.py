"""Exact Skill version lineage and execution attribution tests."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novare.agent_loop import AgentLoop
from novare.llm_client import LLMResponse
from novare.recovery.state import RecoveryState, RunStatus
from novare.session import Session
from novare.tools.registry import ToolRegistry
from web.backend.db.models import User
from web.backend.repositories.skill_version_repo import SkillVersionRepository
from web.backend.routes.chat import _resolve_skill_invocation


BASE = """---
name: demo-skill
description: demo
---

Handle $ARGUMENTS safely.
"""

CHANGED = BASE + "Record the verification result.\n"


@pytest.mark.asyncio
async def test_skill_versions_form_lineage_and_switch_active_version(db_session):
    user = User(
        id=uuid.uuid4(),
        username=f"version_{uuid.uuid4().hex[:8]}",
        email=f"version_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="not-used",
    )
    db_session.add(user)
    await db_session.flush()
    repo = SkillVersionRepository(db_session, user.id)

    v1 = await repo.ensure_version(
        skill_name="demo-skill",
        content=BASE,
        source_kind="discovered",
        source_path="/skills/demo-skill/SKILL.md",
        activate=True,
    )
    v2 = await repo.ensure_version(
        skill_name="demo-skill",
        content=CHANGED,
        source_kind="proposal",
        source_path="/user/demo-skill/SKILL.md",
        activate=True,
    )
    same_v2 = await repo.ensure_version(
        skill_name="demo-skill",
        content=CHANGED,
        source_kind="discovered",
        activate=True,
    )

    assert v1.version == 1
    assert v1.is_active is False
    assert v2.version == 2
    assert v2.parent_version_id == v1.id
    assert v2.is_active is True
    assert same_v2.id == v2.id

    execution = await repo.record_execution(
        version_id=v2.id,
        session_id=None,
        run_id="run-1",
        turn_id="turn-1",
        selection_mode="automatic",
        outcome="success",
        score=0.9,
        verification_status="verified",
        run_status="completed",
        metrics={"iterations": 2, "unsafe": {"not": "stored"}},
    )
    await db_session.flush()

    assert execution.skill_version_id == v2.id
    assert execution.content_sha256 == v2.content_sha256
    assert execution.selection_mode == "automatic"
    assert execution.metrics == {"iterations": 2}


def test_agent_loop_builds_content_free_exact_version_attribution():
    state = RecoveryState()
    state.set_run_status(RunStatus.COMPLETED)
    state.increment_iteration()
    payload = AgentLoop._build_skill_execution_attribution(
        skill_context={
            "skill_name": "demo-skill",
            "version_id": str(uuid.uuid4()),
            "content_sha256": "a" * 64,
            "content": "must not leak",
        },
        recovery_state=state,
        verification={"status": "verified", "risk_score": 0.1},
        session_id="session-1",
    )

    assert payload["outcome"] == "success"
    assert payload["score"] == 0.9
    assert payload["run_status"] == "completed"
    assert "content" not in payload


@pytest.mark.asyncio
async def test_agent_loop_emits_one_attribution_when_skill_finishes():
    llm = AsyncMock()
    llm.collect_stream = AsyncMock(return_value=LLMResponse(
        content="done",
        tool_calls=[],
        stop_reason="stop",
        usage={},
    ))
    loop = AgentLoop(
        llm_client=llm,
        tool_registry=ToolRegistry(),
        system_prompt="test",
    )
    captured = []

    await loop.run_turn(
        Session(session_id="session-1"),
        "rendered skill prompt",
        skill_context={
            "skill_name": "demo-skill",
            "version_id": str(uuid.uuid4()),
            "content_sha256": "b" * 64,
        },
        on_skill_execution=captured.append,
    )

    assert len(captured) == 1
    assert captured[0]["skill_name"] == "demo-skill"
    assert captured[0]["outcome"] == "uncertain"
    assert captured[0]["score"] == 0.5


def test_web_slash_command_uses_user_skill_override(monkeypatch, tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("NOVARE_WORKSPACE", str(workspace_root))
    global_root = tmp_path / "global"
    global_file = global_root / "demo-skill" / "SKILL.md"
    global_file.parent.mkdir(parents=True)
    global_file.write_text(BASE.replace("safely", "globally"), encoding="utf-8")
    user_file = workspace_root / "user-1" / ".novare" / "skills" / "demo-skill" / "SKILL.md"
    user_file.parent.mkdir(parents=True)
    user_file.write_text(BASE, encoding="utf-8")

    rendered, context = _resolve_skill_invocation(
        "/demo-skill evidence query",
        user_id="user-1",
        config=SimpleNamespace(skill_dirs=[global_root]),
    )

    assert rendered == "Handle evidence query safely."
    assert context["skill_name"] == "demo-skill"
    assert context["content"] == BASE
    assert Path(context["source_path"]) == user_file.resolve()
