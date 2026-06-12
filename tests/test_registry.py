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


class TestDefaultToolContext:
    """C1: set_default_tool_context 替代 monkey patch"""

    def test_set_default_tool_context_stores_dict(self, tmp_workspace):
        """set_default_tool_context 存储上下文。"""
        registry = ToolRegistry(workspace=tmp_workspace)
        ctx = {"foo": "bar"}
        registry.set_default_tool_context(ctx)
        assert registry._default_tool_context == {"foo": "bar"}
        # 修改原 dict 不影响已存储的副本
        ctx["baz"] = 1
        assert "baz" not in registry._default_tool_context

    def test_set_default_tool_context_none_clears(self, tmp_workspace):
        """传 None 清空默认上下文。"""
        registry = ToolRegistry(workspace=tmp_workspace)
        registry.set_default_tool_context({"a": 1})
        registry.set_default_tool_context(None)
        assert registry._default_tool_context == {}

    @pytest.mark.asyncio
    async def test_execute_merges_default_and_call_context(self, tmp_workspace):
        """execute 合并默认上下文和调用时上下文，调用时优先。"""
        registry = ToolRegistry(workspace=tmp_workspace)
        captured_kwargs = {}

        async def spy_handler(args, **kwargs):
            captured_kwargs.update(kwargs)
            return "ok"

        registry.register_tool(ToolDef(
            name="spy_tool",
            description="spy",
            parameters={"type": "object", "properties": {}},
            handler=spy_handler,
            source="builtin:context",
        ))

        registry.set_default_tool_context({"alpha": 1, "beta": 2})
        await registry.execute("spy_tool", {}, tool_context={"beta": 99, "gamma": 3})

        assert captured_kwargs["alpha"] == 1
        assert captured_kwargs["beta"] == 99   # 调用时覆盖默认
        assert captured_kwargs["gamma"] == 3
        assert captured_kwargs["workspace"] == tmp_workspace

    @pytest.mark.asyncio
    async def test_execute_no_context_for_plain_builtin(self, tmp_workspace):
        """普通 builtin 工具不注入默认上下文。"""
        registry = ToolRegistry(workspace=tmp_workspace)
        captured_kwargs = {}

        async def spy_handler(args, **kwargs):
            captured_kwargs.update(kwargs)
            return "ok"

        registry.register_tool(ToolDef(
            name="spy_plain",
            description="spy",
            parameters={"type": "object", "properties": {}},
            handler=spy_handler,
            source="builtin",  # 普通 builtin，不是 builtin:context
        ))

        registry.set_default_tool_context({"secret": "value"})
        await registry.execute("spy_plain", {})

        assert "secret" not in captured_kwargs
        assert captured_kwargs["workspace"] == tmp_workspace

    @pytest.mark.asyncio
    async def test_reviewer_evaluate_gets_default_context(self, tmp_workspace):
        """reviewer_evaluate 通过默认上下文也能拿到 reviewer_llm。"""
        registry = ToolRegistry(workspace=tmp_workspace)
        fake_llm = _make_fake_reviewer_llm()

        # 只设默认上下文，不传 tool_context
        registry.set_default_tool_context({"reviewer_llm": fake_llm})

        args = {
            "topic": "test",
            "stage": "candidates",
            "candidates": [{"title": "t", "problem": "p", "idea": "i"}],
        }
        result = await registry.execute("reviewer_evaluate", args)
        fake_llm.collect_stream.assert_awaited_once()
        assert "评审模型未配置" not in result

    def test_register_subagent_tools_no_monkey_patch(self, tmp_workspace, monkeypatch):
        """register_subagent_tools 后 execute 仍是原方法（非 monkey patch）。"""
        # 导入链需要 DATABASE_URL（session.py → db → base.py）
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
        from novare.subagents.registry import SubagentRegistry
        from novare.subagents.tools import register_subagent_tools
        from unittest.mock import MagicMock

        registry = ToolRegistry(workspace=tmp_workspace)
        # 记录原始的底层函数
        original_func = registry.execute.__func__

        sub_reg = SubagentRegistry()
        fake_llm = MagicMock()
        register_subagent_tools(
            tool_registry=registry,
            subagent_registry=sub_reg,
            llm_client=fake_llm,
            system_prompt="test",
            workspace=tmp_workspace,
        )

        # execute 的底层函数不应改变（不是闭包替换）
        assert registry.execute.__func__ is original_func
        # 默认上下文已被设置
        assert registry._default_tool_context.get("subagent_registry") is sub_reg
