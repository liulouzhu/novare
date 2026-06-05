"""tests/test_agent_loop.py"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from novare.agent_loop import AgentLoop
from novare.llm_client import LLMResponse, ToolCall
from novare.tools.registry import ToolRegistry, ToolDef


class TestAgentLoop:
    def _make_loop(self, responses: list[LLMResponse], tool_handler=None):
        llm = AsyncMock()
        # agent_loop 现在调用 collect_stream（非生成器，直接返回 LLMResponse）
        llm.collect_stream = AsyncMock(side_effect=responses)
        llm.close = AsyncMock()

        registry = ToolRegistry()
        if tool_handler:
            for name, handler in tool_handler.items():
                registry.register_tool(ToolDef(
                    name=name,
                    description=f"test {name}",
                    parameters={"type": "object", "properties": {}},
                    handler=handler,
                ))

        return AgentLoop(
            llm_client=llm,
            tool_registry=registry,
            system_prompt="You are a test assistant.",
        )

    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        loop = self._make_loop([
            LLMResponse(content="Hello!", tool_calls=[], stop_reason="stop", usage={}),
        ])
        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "Hi")
        assert result == "Hello!"
        assert len(session.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self):
        async def mock_echo(args, workspace=None):
            return f"Echo: {args.get('message', '')}"

        loop = self._make_loop(
            [
                LLMResponse(content="", tool_calls=[
                    ToolCall(id="call_1", name="echo", arguments={"message": "test"})
                ], stop_reason="tool_calls", usage={}),
                LLMResponse(content="Done!", tool_calls=[], stop_reason="stop", usage={}),
            ],
            tool_handler={"echo": mock_echo},
        )
        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "Echo test")
        assert result == "Done!"
        # user + assistant(tool_calls) + tool_result + assistant(final)
        assert len(session.messages) == 4

    @pytest.mark.asyncio
    async def test_max_iterations_returns_fallback(self):
        async def forever_loop(args, workspace=None):
            return "ok"

        responses = []
        for i in range(25):  # more than max_iterations=20
            responses.append(LLMResponse(content="", tool_calls=[
                ToolCall(id=f"call_{i}", name="echo", arguments={"message": str(i)})
            ], stop_reason="tool_calls", usage={}))

        loop = self._make_loop(responses, tool_handler={"echo": forever_loop})
        loop.max_iterations = 20

        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "go")
        assert "最大迭代" in result or "迭代" in result or "重试" in result

    @pytest.mark.asyncio
    async def test_tool_error_is_reported_to_llm(self):
        async def failing_tool(args, workspace=None):
            raise ValueError("something broke")

        loop = self._make_loop(
            [
                LLMResponse(content="", tool_calls=[
                    ToolCall(id="call_1", name="fail_tool", arguments={})
                ], stop_reason="tool_calls", usage={}),
                LLMResponse(content="Tool had an error", tool_calls=[], stop_reason="stop", usage={}),
            ],
            tool_handler={"fail_tool": failing_tool},
        )
        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "break it")
        assert result == "Tool had an error"
        # Check tool result message contains error info
        tool_msg = session.messages[2]
        assert "Error" in tool_msg["content"]
