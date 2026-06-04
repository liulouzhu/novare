"""tests/test_registry.py"""

import pytest

from novare.tools.registry import ToolRegistry, ToolDef


class TestToolDef:
    def test_to_openai_tool(self):
        tool = ToolDef(
            name="read_file",
            description="Read a file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=None,
        )
        openai_tool = tool.to_openai_tool()
        assert openai_tool["type"] == "function"
        assert openai_tool["function"]["name"] == "read_file"
        assert openai_tool["function"]["description"] == "Read a file"


class TestToolRegistry:
    def test_register_builtin_tools(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "read_file" in names
        assert "write_file" in names
        assert "edit_file" in names
        assert "glob_search" in names
        assert "grep_search" in names

    @pytest.mark.asyncio
    async def test_execute_builtin_tool(self, tmp_workspace):
        (tmp_workspace / "test.txt").write_text("hello", encoding="utf-8")
        registry = ToolRegistry(workspace=tmp_workspace)
        result = await registry.execute("read_file", {"path": str(tmp_workspace / "test.txt")})
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        result = await registry.execute("nonexistent_tool", {})
        assert "Error" in result

    def test_to_openai_tools(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        tools = registry.to_openai_tools()
        assert len(tools) >= 5
        assert all(t["type"] == "function" for t in tools)

    def test_register_mcp_tools(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        registry.register_tool(ToolDef(
            name="paper_search",
            description="Search papers",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            handler=None,
        ))
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "paper_search" in names
