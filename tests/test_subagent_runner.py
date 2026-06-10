"""tests/test_subagent_runner.py — run_subagent 集成测试"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from novare.llm_client import LLMResponse, ToolCall
from novare.subagents.registry import SubagentRegistry
from novare.subagents.runner import run_subagent
from novare.subagents.types import SubagentType, SubagentStatus
from novare.tools.registry import ToolRegistry, ToolDef


def _make_parent_registry(tool_names: dict[str, AsyncMock] | None = None) -> ToolRegistry:
    """创建带模拟工具的父注册表"""
    registry = ToolRegistry()
    handlers = tool_names or {
        "paper_search": AsyncMock(return_value='{"papers": [{"title": "Test Paper"}]}'),
        "rag_query": AsyncMock(return_value="relevant context from RAG"),
        "read_file": AsyncMock(return_value="file content here"),
        "code_execute": AsyncMock(return_value="print('hello')\nhello"),
        "glob_search": AsyncMock(return_value="file1.py\nfile2.py"),
        "grep_search": AsyncMock(return_value="match found"),
    }
    for name, handler in handlers.items():
        registry.register_tool(ToolDef(
            name=name,
            description=f"Mock {name}",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        ))
    return registry


class TestRunSubagent:
    @pytest.mark.asyncio
    async def test_basic_search_subagent(self):
        """搜索子智能体应该能使用 paper_search 完成任务"""
        parent = _make_parent_registry()
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "搜索 Transformer 论文")

        llm = AsyncMock()
        # 第一次调用：LLM 调用 paper_search
        # 第二次调用：LLM 返回最终回答
        llm.collect_stream = AsyncMock(side_effect=[
            LLMResponse(content="", tool_calls=[
                ToolCall(id="tc_1", name="paper_search", arguments={"query": "Transformer attention"})
            ], stop_reason="tool_calls", usage={}),
            LLMResponse(content="找到 1 篇论文：Test Paper", tool_calls=[], stop_reason="stop", usage={}),
        ])

        result = await run_subagent(
            subagent_id=record.subagent_id,
            task="搜索 Transformer 论文",
            subagent_type=SubagentType.SEARCH,
            parent_registry=parent,
            llm_client=llm,
            system_prompt="You are a research assistant.",
            registry=registry,
            max_iterations=5,
        )

        assert "Test Paper" in result
        assert registry.get(record.subagent_id).status == SubagentStatus.COMPLETED
        assert registry.get(record.subagent_id).tool_calls_made == 1

    @pytest.mark.asyncio
    async def test_disallowed_tool_returns_error_in_result(self):
        """子智能体尝试使用不允许的工具时，工具返回错误"""
        parent = _make_parent_registry()
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")

        llm = AsyncMock()
        llm.collect_stream = AsyncMock(side_effect=[
            LLMResponse(content="", tool_calls=[
                ToolCall(id="tc_1", name="write_file", arguments={"path": "/test", "content": "data"})
            ], stop_reason="tool_calls", usage={}),
            # LLM sees the error and responds
            LLMResponse(content="无法写入文件，工具不允许", tool_calls=[], stop_reason="stop", usage={}),
        ])

        result = await run_subagent(
            subagent_id=record.subagent_id,
            task="test",
            subagent_type=SubagentType.SEARCH,
            parent_registry=parent,
            llm_client=llm,
            system_prompt="test",
            registry=registry,
        )

        # The subagent should still complete (the error is returned to LLM as tool result)
        assert registry.get(record.subagent_id).status == SubagentStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_subagent_failure_records_error(self):
        """LLM 调用异常时，子智能体标记为 FAILED"""
        parent = _make_parent_registry()
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")

        llm = AsyncMock()
        llm.collect_stream = AsyncMock(side_effect=RuntimeError("API connection failed"))

        result = await run_subagent(
            subagent_id=record.subagent_id,
            task="test",
            subagent_type=SubagentType.SEARCH,
            parent_registry=parent,
            llm_client=llm,
            system_prompt="test",
            registry=registry,
        )

        assert "Error" in result
        assert registry.get(record.subagent_id).status == SubagentStatus.FAILED
        assert "API connection failed" in registry.get(record.subagent_id).error

    @pytest.mark.asyncio
    async def test_subagent_with_context(self):
        """子智能体应能接收额外上下文"""
        parent = _make_parent_registry()
        registry = SubagentRegistry()
        record = registry.create(SubagentType.ANALYZER, "分析论文")

        llm = AsyncMock()
        llm.collect_stream = AsyncMock(return_value=LLMResponse(
            content="分析完成",
            tool_calls=[],
            stop_reason="stop",
            usage={},
        ))

        result = await run_subagent(
            subagent_id=record.subagent_id,
            task="分析论文",
            subagent_type=SubagentType.ANALYZER,
            parent_registry=parent,
            llm_client=llm,
            system_prompt="test",
            registry=registry,
            context={"paper_ids": ["arxiv:2301.00001", "doi:10.1234/test"]},
        )

        assert result == "分析完成"
        # Verify the system prompt was augmented with context
        call_args = llm.collect_stream.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        assert "paper_ids" in system_msg

    @pytest.mark.asyncio
    async def test_subagent_system_prompt_includes_type(self):
        """子智能体的系统提示词应包含类型和任务"""
        parent = _make_parent_registry()
        registry = SubagentRegistry()
        record = registry.create(SubagentType.EXPLORER, "探索项目结构")

        llm = AsyncMock()
        llm.collect_stream = AsyncMock(return_value=LLMResponse(
            content="探索完成",
            tool_calls=[],
            stop_reason="stop",
            usage={},
        ))

        await run_subagent(
            subagent_id=record.subagent_id,
            task="探索项目结构",
            subagent_type=SubagentType.EXPLORER,
            parent_registry=parent,
            llm_client=llm,
            system_prompt="你是科研助手。",
            registry=registry,
        )

        call_args = llm.collect_stream.call_args
        messages = call_args[0][0]
        system_msg = messages[0]["content"]
        assert "explorer" in system_msg
        assert "探索项目结构" in system_msg
