"""tests/test_registry.py"""

import json
from unittest.mock import AsyncMock

import pytest

from novare.tools.registry import ToolRegistry, ToolDef
from novare.tools.reviewer_evaluate import handle_reviewer_evaluate
from novare.llm_client import LLMResponse


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


def _make_fake_reviewer_llm(response_content: str = '{"reviews": []}'):
    """构造一个 mock LLMClient，collect_stream 返回指定内容。"""
    fake = AsyncMock()
    fake.collect_stream.return_value = LLMResponse(
        content=response_content,
        tool_calls=[],
        stop_reason="stop",
    )
    return fake


class TestReviewerEvaluate:
    """验证 reviewer_evaluate 能正确拿到 reviewer_llm。"""

    @pytest.mark.asyncio
    async def test_registry_execute_passes_reviewer_llm(self, tmp_workspace):
        """通过 registry.execute + tool_context 传入 reviewer_llm 应该生效。"""
        fake_llm = _make_fake_reviewer_llm()
        registry = ToolRegistry(workspace=tmp_workspace)
        args = {
            "topic": "test topic",
            "stage": "candidates",
            "candidates": [{"title": "idea", "problem": "p", "idea": "i"}],
        }
        result = await registry.execute(
            "reviewer_evaluate", args,
            tool_context={"reviewer_llm": fake_llm},
        )
        fake_llm.collect_stream.assert_awaited_once()
        assert "评审模型未配置" not in result

    @pytest.mark.asyncio
    async def test_handle_direct_kwargs(self, tmp_workspace):
        """直接调用 handler，reviewer_llm 作为顶层 kwargs 传入。"""
        fake_llm = _make_fake_reviewer_llm()
        args = {
            "topic": "test",
            "stage": "candidates",
            "candidates": [{"title": "t", "problem": "p", "idea": "i"}],
        }
        result = await handle_reviewer_evaluate(args, reviewer_llm=fake_llm)
        fake_llm.collect_stream.assert_awaited_once()
        assert "评审模型未配置" not in result

    @pytest.mark.asyncio
    async def test_handle_legacy_tool_context_kwarg(self, tmp_workspace):
        """兼容旧格式：kwargs["tool_context"]["reviewer_llm"]。"""
        fake_llm = _make_fake_reviewer_llm()
        args = {
            "topic": "test",
            "stage": "candidates",
            "candidates": [{"title": "t", "problem": "p", "idea": "i"}],
        }
        result = await handle_reviewer_evaluate(args, tool_context={"reviewer_llm": fake_llm})
        fake_llm.collect_stream.assert_awaited_once()
        assert "评审模型未配置" not in result

    @pytest.mark.asyncio
    async def test_no_reviewer_llm_returns_error(self, tmp_workspace):
        """未传 reviewer_llm 时应返回配置提示。"""
        args = {
            "topic": "test",
            "stage": "candidates",
            "candidates": [{"title": "t", "problem": "p", "idea": "i"}],
        }
        result = await handle_reviewer_evaluate(args)
        assert "评审模型未配置" in result
