"""novare/agent_loop.py — 核心 agent 循环"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from novare.llm_client import LLMClient
    from novare.tools.registry import ToolRegistry

logger = logging.getLogger("novare.loop")


class AgentLoop:
    """等价于 Claw Code 的 ConversationRuntime.run_turn()"""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        system_prompt: str = "",
        max_iterations: int = 20,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations

    async def run_turn(
        self,
        session,
        user_input: str,
        on_text: Callable[[str], None] | None = None,
    ) -> str:
        """执行一轮对话：用户输入 → LLM（流式） → 工具循环 → 最终回答

        on_text: 可选回调，流式输出时逐 chunk 调用，用于实时打印文本。
        """
        session.add_user_message(user_input)

        for iteration in range(self.max_iterations):
            # 构建消息
            messages = self._build_messages(session)

            # 流式调用 LLM，on_text 实时输出
            tools = self.tool_registry.to_openai_tools()
            response = await self.llm_client.collect_stream(
                messages, tools=tools, on_text=on_text,
            )

            # 如果没有工具调用，返回最终回答
            if not response.tool_calls:
                session.add_assistant_message(response.content)
                return response.content

            # 有工具调用：记录 assistant 消息（含 tool_calls）
            tool_calls_dicts = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in response.tool_calls
            ]
            session.add_assistant_message(response.content or "", tool_calls=tool_calls_dicts)

            # 执行每个工具调用
            for tc in response.tool_calls:
                logger.info("Tool call: %s(%s)", tc.name, tc.arguments)
                try:
                    result = await self.tool_registry.execute(tc.name, tc.arguments)
                except Exception as e:
                    logger.exception("Tool error: %s", tc.name)
                    result = f"Error: {e}"
                session.add_tool_result(tc.id, result)
                logger.debug("Tool result: %s → %d chars", tc.name, len(result))

        return "达到最大迭代次数（{}），请简化问题后重试。".format(self.max_iterations)

    def _build_messages(self, session) -> list[dict]:
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(session.messages)
        return messages
