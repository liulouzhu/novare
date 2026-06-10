"""tests/test_subagent_executor.py — SubagentToolExecutor 测试"""

import pytest
from unittest.mock import AsyncMock

from novare.tools.registry import ToolRegistry, ToolDef
from novare.subagents.tool_executor import SubagentToolExecutor


def _make_registry_with_tools(tool_names: list[str]) -> ToolRegistry:
    """创建带指定工具的 ToolRegistry"""
    registry = ToolRegistry()
    for name in tool_names:
        handler = AsyncMock(return_value=f"result from {name}")
        registry.register_tool(ToolDef(
            name=name,
            description=f"Test tool {name}",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        ))
    return registry


class TestSubagentToolExecutor:
    def test_filters_openai_tools(self):
        parent = _make_registry_with_tools(["read_file", "write_file", "paper_search"])
        executor = SubagentToolExecutor(parent, {"read_file", "paper_search"})

        tools = executor.to_openai_tools()
        names = {t["function"]["name"] for t in tools}
        assert names == {"read_file", "paper_search"}
        assert "write_file" not in names

    def test_empty_allowlist_filters_all(self):
        parent = _make_registry_with_tools(["read_file", "write_file"])
        executor = SubagentToolExecutor(parent, set())

        tools = executor.to_openai_tools()
        assert len(tools) == 0

    @pytest.mark.asyncio
    async def test_execute_allowed_tool(self):
        parent = _make_registry_with_tools(["read_file", "write_file"])
        executor = SubagentToolExecutor(parent, {"read_file"})

        result = await executor.execute("read_file", {"path": "/test"})
        assert "result from read_file" in result

    @pytest.mark.asyncio
    async def test_execute_disallowed_tool(self):
        parent = _make_registry_with_tools(["read_file", "write_file"])
        executor = SubagentToolExecutor(parent, {"read_file"})

        result = await executor.execute("write_file", {"path": "/test", "content": "x"})
        assert "Error" in result
        assert "not allowed" in result
        assert "write_file" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        parent = _make_registry_with_tools(["read_file"])
        executor = SubagentToolExecutor(parent, {"read_file"})

        # Unknown tool is also not in allowlist
        result = await executor.execute("nonexistent", {})
        assert "Error" in result
        assert "not allowed" in result

    def test_allowed_tools_property(self):
        parent = _make_registry_with_tools(["a", "b", "c"])
        executor = SubagentToolExecutor(parent, {"a", "c"})
        assert executor.allowed_tools == {"a", "c"}

    def test_repr(self):
        parent = _make_registry_with_tools(["a", "b"])
        executor = SubagentToolExecutor(parent, {"a"})
        assert "1 tools" in repr(executor)
