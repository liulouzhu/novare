"""Progressive Skill discovery and autonomous loading tests."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from novare.agent_loop import AgentLoop
from novare.llm_client import LLMResponse, ToolCall
from novare.session import Session
from novare.tools.registry import ToolRegistry
from novare.tools.skills import skill_catalog_prompt


GLOBAL_SKILL = """---
name: research-flow
description: Search and verify research evidence
---

First search for $ARGUMENTS, then verify every claim.
"""

USER_SKILL = GLOBAL_SKILL.replace("every claim", "each important claim")


def _write_skill(root: Path, content: str) -> Path:
    path = root / "research-flow" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_skills_list_returns_metadata_only_and_honors_user_shadowing(tmp_path):
    user_root = tmp_path / "user"
    global_root = tmp_path / "global"
    _write_skill(global_root, GLOBAL_SKILL)
    _write_skill(user_root, USER_SKILL)
    registry = ToolRegistry(tmp_path)

    raw = await registry.execute(
        "skills_list",
        {"query": "research"},
        tool_context={"skill_roots": [str(user_root), str(global_root)]},
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["data"]["count"] == 1
    assert payload["data"]["skills"] == [{
        "name": "research-flow",
        "description": "Search and verify research evidence",
    }]
    assert "First search" not in raw
    assert "SKILL.md" not in raw


@pytest.mark.asyncio
async def test_skill_view_loads_body_registers_version_and_marks_automatic(tmp_path):
    root = tmp_path / "skills"
    source = _write_skill(root, GLOBAL_SKILL)
    version_id = str(uuid.uuid4())
    register = AsyncMock(return_value={
        "skill_name": "research-flow",
        "version_id": version_id,
        "content_sha256": "a" * 64,
        "selection_mode": "automatic",
    })
    loaded: list[dict] = []
    registry = ToolRegistry(tmp_path)

    raw = await registry.execute(
        "skill_view",
        {"name": "research-flow", "arguments": "graph agents"},
        tool_context={
            "skill_roots": [str(root)],
            "register_skill_version": register,
            "loaded_skill_versions": loaded,
        },
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["data"]["instructions"] == (
        "First search for graph agents, then verify every claim."
    )
    assert payload["data"]["skill"]["version_id"] == version_id
    assert payload["data"]["skill"]["selection_mode"] == "automatic"
    assert loaded == [{
        "skill_name": "research-flow",
        "version_id": version_id,
        "content_sha256": "a" * 64,
        "selection_mode": "automatic",
    }]
    register.assert_awaited_once_with(
        skill_name="research-flow",
        content=GLOBAL_SKILL,
        source_path=str(source.resolve()),
        selection_mode="automatic",
    )
    assert str(source.resolve()) not in raw


def test_catalog_prompt_contains_metadata_but_not_skill_body(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, GLOBAL_SKILL)

    prompt = skill_catalog_prompt([root])

    assert "research-flow" in prompt
    assert "Search and verify research evidence" in prompt
    assert "First search for" not in prompt
    assert "skill_view" in prompt


@pytest.mark.asyncio
async def test_agent_selected_skill_is_attributed_after_turn(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, GLOBAL_SKILL)
    version_id = str(uuid.uuid4())
    loaded: list[dict] = []

    async def register_skill_version(**_kwargs):
        return {
            "skill_name": "research-flow",
            "version_id": version_id,
            "content_sha256": "b" * 64,
            "selection_mode": "automatic",
        }

    llm = AsyncMock()
    llm.collect_stream = AsyncMock(side_effect=[
        LLMResponse(
            content="",
            tool_calls=[ToolCall(
                id="skill-call-1",
                name="skill_view",
                arguments={"name": "research-flow", "arguments": "graph agents"},
            )],
            stop_reason="tool_calls",
            usage={},
        ),
        LLMResponse(content="completed", tool_calls=[], stop_reason="stop", usage={}),
    ])
    registry = ToolRegistry(tmp_path)
    loop = AgentLoop(llm_client=llm, tool_registry=registry, system_prompt="test")
    captured: list[dict] = []
    tool_context = {
        "skill_roots": [str(root)],
        "register_skill_version": register_skill_version,
        "loaded_skill_versions": loaded,
    }

    result = await loop.run_turn(
        Session(session_id="session-auto"),
        "research graph agents",
        tool_context=tool_context,
        on_skill_execution=captured.append,
    )

    assert result == "completed"
    assert len(captured) == 1
    assert captured[0]["version_id"] == version_id
    assert captured[0]["metrics"]["selection_mode"] == "automatic"
    assert captured[0]["outcome"] == "uncertain"


@pytest.mark.asyncio
async def test_skill_view_unknown_name_fails_closed(tmp_path):
    registry = ToolRegistry(tmp_path)
    raw = await registry.execute(
        "skill_view",
        {"name": "missing"},
        tool_context={"skill_roots": [str(tmp_path / "skills")]},
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error_code"] == "SKILL_NOT_FOUND"
