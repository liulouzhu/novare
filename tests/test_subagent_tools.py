"""tests/test_subagent_tools.py — spawn/check/list 工具处理器测试"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from novare.llm_client import LLMResponse
from novare.subagents.registry import SubagentRegistry
from novare.subagents.tools import (
    handle_spawn_subagent,
    handle_check_subagent,
    handle_list_subagents,
    register_subagent_tools,
)
from novare.subagents.types import SubagentType, SubagentStatus
from novare.tools.registry import ToolRegistry, ToolDef


def _make_tool_context(registry: SubagentRegistry | None = None) -> dict:
    """构建模拟的 tool_context"""
    reg = registry or SubagentRegistry()
    parent = ToolRegistry()
    # Add some tools so the subagent has something to work with
    parent.register_tool(ToolDef(
        name="paper_search",
        description="Search papers",
        parameters={"type": "object", "properties": {}},
        handler=AsyncMock(return_value="found papers"),
    ))
    parent.register_tool(ToolDef(
        name="read_file",
        description="Read file",
        parameters={"type": "object", "properties": {}},
        handler=AsyncMock(return_value="file content"),
    ))

    llm = AsyncMock()
    llm.collect_stream = AsyncMock(return_value=LLMResponse(
        content="subagent result",
        tool_calls=[],
        stop_reason="stop",
        usage={},
    ))

    return {
        "subagent_registry": reg,
        "parent_tool_registry": parent,
        "llm_client": llm,
        "system_prompt": "test system prompt",
        "workspace": MagicMock(),
    }


class TestHandleSpawnSubagent:
    @pytest.mark.asyncio
    async def test_spawn_returns_immediately(self):
        """默认异步模式应立即返回 subagent_id"""
        ctx = _make_tool_context()
        result_str = await handle_spawn_subagent(
            {"subagent_type": "search", "task": "test task"},
            **ctx,
        )
        result = json.loads(result_str)

        assert "subagent_id" in result
        assert result["status"] == "running"
        assert result["type"] == "search"

        # Verify the subagent was registered
        registry: SubagentRegistry = ctx["subagent_registry"]
        record = registry.get(result["subagent_id"])
        assert record is not None
        assert record.type == SubagentType.SEARCH

    @pytest.mark.asyncio
    async def test_spawn_await_result(self):
        """await_result=true 应等待子智能体完成"""
        ctx = _make_tool_context()
        result_str = await handle_spawn_subagent(
            {"subagent_type": "search", "task": "test task", "await_result": True},
            **ctx,
        )
        result = json.loads(result_str)

        assert result["status"] == "completed"
        assert "result" in result

    @pytest.mark.asyncio
    async def test_spawn_empty_task_returns_error(self):
        ctx = _make_tool_context()
        result_str = await handle_spawn_subagent(
            {"subagent_type": "search", "task": ""},
            **ctx,
        )
        result = json.loads(result_str)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_spawn_invalid_type_returns_error(self):
        ctx = _make_tool_context()
        result_str = await handle_spawn_subagent(
            {"subagent_type": "invalid_type", "task": "test"},
            **ctx,
        )
        result = json.loads(result_str)
        assert "error" in result
        assert "无效" in result["error"]

    @pytest.mark.asyncio
    async def test_spawn_missing_context_returns_error(self):
        """缺少必要的 tool_context 时返回错误"""
        result_str = await handle_spawn_subagent(
            {"subagent_type": "search", "task": "test"},
            # No kwargs provided
        )
        result = json.loads(result_str)
        assert "error" in result


class TestHandleCheckSubagent:
    @pytest.mark.asyncio
    async def test_check_running_subagent(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")

        async def long_task():
            await asyncio.sleep(100)

        await registry.start(record.subagent_id, long_task())

        result_str = await handle_check_subagent(
            {"subagent_id": record.subagent_id},
            subagent_registry=registry,
        )
        result = json.loads(result_str)

        assert result["status"] == "running"
        assert "elapsed_seconds" in result

    @pytest.mark.asyncio
    async def test_check_completed_subagent(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")
        registry.complete(record.subagent_id, "found 5 papers")

        result_str = await handle_check_subagent(
            {"subagent_id": record.subagent_id},
            subagent_registry=registry,
        )
        result = json.loads(result_str)

        assert result["status"] == "completed"
        assert result["result"] == "found 5 papers"

    @pytest.mark.asyncio
    async def test_check_failed_subagent(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")
        registry.fail(record.subagent_id, "API error")

        result_str = await handle_check_subagent(
            {"subagent_id": record.subagent_id},
            subagent_registry=registry,
        )
        result = json.loads(result_str)

        assert result["status"] == "failed"
        assert result["error"] == "API error"

    @pytest.mark.asyncio
    async def test_check_nonexistent_returns_error(self):
        registry = SubagentRegistry()
        result_str = await handle_check_subagent(
            {"subagent_id": "sa-nonexistent"},
            subagent_registry=registry,
        )
        result = json.loads(result_str)
        assert "error" in result
        assert "未找到" in result["error"]


class TestHandleListSubagents:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        registry = SubagentRegistry()
        result_str = await handle_list_subagents({}, subagent_registry=registry)
        result = json.loads(result_str)
        assert result["subagents"] == []

    @pytest.mark.asyncio
    async def test_list_multiple(self):
        registry = SubagentRegistry()
        registry.create(SubagentType.SEARCH, "task 1")
        registry.create(SubagentType.ANALYZER, "task 2")

        result_str = await handle_list_subagents({}, subagent_registry=registry)
        result = json.loads(result_str)

        assert len(result["subagents"]) == 2
        types = {s["type"] for s in result["subagents"]}
        assert types == {"search", "analyzer"}


class TestRegisterSubagentTools:
    def test_registers_three_tools(self):
        registry = ToolRegistry()
        subagent_registry = SubagentRegistry()

        # Add some dummy tools to the parent registry so the executor has something to work with
        registry.register_tool(ToolDef(
            name="paper_search",
            description="Search",
            parameters={"type": "object", "properties": {}},
            handler=AsyncMock(return_value="ok"),
        ))

        initial_count = len(registry.list_tools())

        register_subagent_tools(
            tool_registry=registry,
            subagent_registry=subagent_registry,
            llm_client=AsyncMock(),
            system_prompt="test",
            workspace=MagicMock(),
        )

        new_tools = registry.list_tools()
        assert len(new_tools) == initial_count + 3

        tool_names = {t.name for t in new_tools}
        assert "spawn_subagent" in tool_names
        assert "check_subagent" in tool_names
        assert "list_subagents" in tool_names

    def test_spawn_tool_has_correct_schema(self):
        registry = ToolRegistry()
        register_subagent_tools(
            tool_registry=registry,
            subagent_registry=SubagentRegistry(),
            llm_client=AsyncMock(),
            system_prompt="test",
            workspace=MagicMock(),
        )

        spawn_tool = next(t for t in registry.list_tools() if t.name == "spawn_subagent")
        params = spawn_tool.parameters

        assert "subagent_type" in params["properties"]
        assert "task" in params["properties"]
        assert "await_result" in params["properties"]
        assert params["required"] == ["subagent_type", "task"]
