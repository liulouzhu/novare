"""novare/agent_loop.py — 核心 agent 循环

借鉴 claw-code 的 ConversationRuntime.run_turn() 模式，
支持主智能体和子智能体共用同一循环（通过 duck typing 接受 ToolRegistry 或 SubagentToolExecutor）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol, runtime_checkable

from novare.context_manager import (
    TokenUsage,
    compact_messages,
    estimate_messages_tokens,
    estimate_tools_tokens,
)
from novare.task_state import TaskState, TaskStateManager
from novare.tool_result import parse_tool_result

if TYPE_CHECKING:
    from novare.llm_client import LLMClient


@runtime_checkable
class ToolExecutor(Protocol):
    """工具执行器协议 — ToolRegistry 和 SubagentToolExecutor 都满足此接口"""

    def to_openai_tools(self) -> list[dict]: ...

    async def execute(self, name: str, arguments: dict, tool_context: dict | None = None) -> str: ...

logger = logging.getLogger("novare.loop")


class AgentLoop:
    """等价于 Claw Code 的 ConversationRuntime.run_turn()

    tool_registry 参数接受任何满足 ToolExecutor 协议的对象：
    - ToolRegistry（主智能体，完整工具集）
    - SubagentToolExecutor（子智能体，白名单受限工具集）
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolExecutor,
        system_prompt: str = "",
        max_iterations: int = 20,
        reviewer_llm: LLMClient | None = None,
        auto_compact_threshold: int = 100_000,
        preserve_recent_messages: int = 4,
        turn_timeout: int = 300,
    ):
        self.llm_client = llm_client
        self.tool_registry: ToolExecutor = tool_registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.reviewer_llm = reviewer_llm
        self.auto_compact_threshold = auto_compact_threshold
        self.preserve_recent_messages = preserve_recent_messages
        self.turn_timeout = turn_timeout

    async def run_turn(
        self,
        session,
        user_input: str,
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, str, dict, str | None, float | None], None] | None = None,
        tool_context: dict | None = None,
        on_task_state: Callable[[dict], None] | None = None,
        system_prompt: str | None = None,
        autosave: bool = True,
        on_compact: Callable[[object], Awaitable[None] | None] | None = None,
        should_cancel: Callable[[], Awaitable[bool] | bool] | None = None,
        on_message: Callable[[dict], Awaitable[None] | None] | None = None,
    ) -> str:
        """执行一轮对话，带 per-turn 超时保护。

        system_prompt: 可选的 per-turn 覆盖，不传则使用 self.system_prompt。
        autosave: compact 后是否自动 session.save() 写 JSONL。CLI 默认 True，Web 应传 False。
        on_compact: compact 发生后的回调，上层可用于持久化 compact 后的消息（如写 DB）。
        should_cancel: 可选的协作式取消回调，每轮迭代和工具调用前检查。返回 True 时优雅停止。
        超时时返回友好提示，已执行的工具调用和消息保留在 session 中。
        """
        try:
            return await asyncio.wait_for(
                self._run_turn_core(session, user_input, on_text, on_tool, tool_context, on_task_state, system_prompt, autosave, on_compact, should_cancel, on_message),
                timeout=self.turn_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Turn timed out after %ds (user_input=%s)", self.turn_timeout, user_input[:80])
            return f"本轮任务超时（超过 {self.turn_timeout} 秒），请简化问题或拆分为更小的子任务后重试。"

    async def _run_turn_core(
        self,
        session,
        user_input: str,
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, str, dict, str | None, float | None], None] | None = None,
        tool_context: dict | None = None,
        on_task_state: Callable[[dict], None] | None = None,
        system_prompt: str | None = None,
        autosave: bool = True,
        on_compact: Callable[[object], Awaitable[None] | None] | None = None,
        should_cancel: Callable[[], Awaitable[bool] | bool] | None = None,
        on_message: Callable[[dict], Awaitable[None] | None] | None = None,
    ) -> str:
        """执行一轮对话的核心逻辑：用户输入 → LLM（流式） → 工具循环 → 最终回答

        on_text: 可选回调，流式输出时逐 chunk 调用，用于实时打印文本。
        on_tool: 可选回调，工具状态事件。
                 签名：(event, name, arguments, result_preview, duration_sec)
                 event: "start" | "end" | "error"
        on_task_state: 可选回调，工具循环结束后推送当前任务状态快照。
                       签名：(state_dict: dict)
        system_prompt: 可选的 per-turn 覆盖，不传则使用 self.system_prompt。
        """
        # 当前 turn 使用的 system_prompt（per-turn 覆盖 > 实例默认值）
        effective_prompt = system_prompt if system_prompt is not None else self.system_prompt

        # ── Turn-scoped TaskState：每次 run_turn 独立持有 ──
        task_mgr = TaskStateManager()
        task_mgr.init_turn(user_input)

        try:
            # 将 reviewer_llm 注入 tool_context，供 reviewer_evaluate 工具使用
            if tool_context is None:
                tool_context = {}
            if self.reviewer_llm:
                tool_context["reviewer_llm"] = self.reviewer_llm

            session.add_user_message(user_input)
            await self._emit_message(on_message, session.messages[-1])

            for iteration in range(self.max_iterations):
                # 协作式取消检查：每次迭代开始前
                if should_cancel:
                    _cancel_result = should_cancel()
                    if asyncio.iscoroutine(_cancel_result):
                        _cancel_result = await _cancel_result
                    if _cancel_result:
                        return "任务已取消。"

                # 构建消息（注入当前 task state，可能已被压缩）
                messages = self._build_messages(session, task_state=task_mgr.state, system_prompt=effective_prompt)

                # Preflight：估算 system + messages + tools 是否超过阈值，超过则先压缩
                if await self._preflight_compact(session, messages, task_mgr.state, system_prompt=effective_prompt, autosave=autosave, on_compact=on_compact):
                    messages = self._build_messages(session, task_state=task_mgr.state, system_prompt=effective_prompt)

                # 流式调用 LLM，on_text 实时输出
                tools = self.tool_registry.to_openai_tools()
                response = await self.llm_client.collect_stream(
                    messages, tools=tools, on_text=on_text,
                )

                # 追踪 usage（用于触发自动压缩）
                if response.usage:
                    session.usage_tracker.add(TokenUsage(
                        input_tokens=response.usage.get("prompt_tokens", 0)
                            or response.usage.get("input_tokens", 0),
                        output_tokens=response.usage.get("completion_tokens", 0)
                            or response.usage.get("output_tokens", 0),
                    ))
                    logger.debug("Usage: %s", session.usage_tracker.summary())

                # 如果没有工具调用，检查是否需要压缩后返回
                if not response.tool_calls:
                    session.add_assistant_message(response.content)
                    await self._emit_message(on_message, session.messages[-1])
                    await self._maybe_auto_compact(session, system_prompt=effective_prompt, autosave=autosave, on_compact=on_compact)
                    return response.content

                # 有工具调用：记录 assistant 消息（含 tool_calls）
                tool_calls_dicts = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                    for tc in response.tool_calls
                ]
                session.add_assistant_message(response.content or "", tool_calls=tool_calls_dicts)
                await self._emit_message(on_message, session.messages[-1])

                # 执行每个工具调用
                for tc in response.tool_calls:
                    # 协作式取消检查：每次工具调用前
                    if should_cancel:
                        _cancel_result = should_cancel()
                        if asyncio.iscoroutine(_cancel_result):
                            _cancel_result = await _cancel_result
                        if _cancel_result:
                            return "任务已取消。"

                    logger.info("Tool call: %s(%s)", tc.name, tc.arguments)
                    if on_tool:
                        on_tool("start", tc.name, tc.arguments, None, None)
                    t0 = time.monotonic()
                    result = await self.tool_registry.execute(tc.name, tc.arguments, tool_context=tool_context)
                    elapsed = time.monotonic() - t0
                    # 结构化错误检测（JSON ok 字段优先，降级到 startswith 兼容旧格式）
                    parsed_result = parse_tool_result(result)
                    is_error = not parsed_result.ok
                    if is_error:
                        if on_tool:
                            on_tool("error", tc.name, tc.arguments, result, elapsed)
                    else:
                        if on_tool:
                            on_tool("end", tc.name, tc.arguments, result, elapsed)
                    session.add_tool_result(tc.id, result)
                    await self._emit_message(on_message, session.messages[-1])
                    logger.debug("Tool result: %s → %d chars", tc.name, len(result))

                    # 更新 task state
                    task_mgr.update_from_tool(tc.name, tc.arguments, result)

                # 工具循环结束后推送 task state
                if on_task_state and task_mgr.state:
                    on_task_state(task_mgr.state.to_dict())

                # 每轮工具循环结束后检查是否需要压缩
                await self._maybe_auto_compact(session, system_prompt=effective_prompt, autosave=autosave, on_compact=on_compact)

            return "达到最大迭代次数（{}），请简化问题后重试。".format(self.max_iterations)
        finally:
            # 清理局部状态，避免异常时残留
            task_mgr.clear()

    @staticmethod
    async def _emit_message(
        callback: Callable[[dict], Awaitable[None] | None] | None,
        message: dict,
    ) -> None:
        if callback is None:
            return
        result = callback(dict(message))
        if asyncio.iscoroutine(result):
            await result

    async def run_reviewer(
        self,
        prompt: str,
        on_text: Callable[[str], None] | None = None,
    ) -> str:
        """用评审模型独立评估。不走工具循环，直接返回评审结果。

        用于双模型对抗评审：executor 模型生成候选，reviewer 模型独立打分。
        """
        if not self.reviewer_llm:
            return "Error: 评审模型未配置。请设置 NOVARE_REVIEWER_API_KEY 等环境变量。"

        messages = [
            {"role": "system", "content": "你是一个独立的研究评审专家。请根据提供的候选创新点和相关论文，给出客观的评审意见。输出 JSON 格式。"},
            {"role": "user", "content": prompt},
        ]

        response = await self.reviewer_llm.collect_stream(messages, on_text=on_text)
        return response.content or ""

    def _build_messages(self, session, task_state: TaskState | None = None, system_prompt: str | None = None) -> list[dict]:
        """构建发送给 LLM 的消息列表

        task_state: 可选的任务状态，如果存在则追加到 system prompt 末尾。
        system_prompt: 可选的 per-turn 覆盖，不传则使用 self.system_prompt。
        每次迭代由 run_turn 传入当前 turn 的局部 task state。

        注意：session.messages 可能已被 _maybe_auto_compact() 压缩过，
        此处直接使用，不再重复裁剪。
        """
        effective = system_prompt if system_prompt is not None else self.system_prompt
        messages = []
        if effective:
            system_content = effective
            # 注入任务状态（如果有的话）
            if task_state:
                system_content += "\n\n" + task_state.to_prompt_block()
            messages.append({"role": "system", "content": system_content})
        messages.extend(session.messages)
        return messages

    async def _preflight_compact(self, session, messages: list[dict], task_state: TaskState | None = None, system_prompt: str | None = None, autosave: bool = True, on_compact: Callable[[object], Awaitable[None] | None] | None = None) -> bool:
        """LLM 调用前的预检：估算 system + messages + tools 总量，超过阈值则先压缩。

        解决的问题：post-turn 压缩来不及——如果单次请求本身已超上下文窗口，
        API 会直接报错，根本走不到 _maybe_auto_compact()。

        使用启发式估算（estimate_*），阈值取 auto_compact_threshold 的 80%
        留安全余量，因为启发式会低估 tool schema / framing 的真实开销。
        """
        if self.auto_compact_threshold <= 0:
            return False

        preflight_threshold = int(self.auto_compact_threshold * 0.8)
        tools = self.tool_registry.to_openai_tools()
        estimated = estimate_messages_tokens(messages) + estimate_tools_tokens(tools)

        if estimated < preflight_threshold:
            return False

        logger.info(
            "Preflight compact triggered: estimated=%d (messages=%d, tools=%d), threshold=%d",
            estimated, estimate_messages_tokens(messages), estimate_tools_tokens(tools), preflight_threshold,
        )

        compacted, did_compact = compact_messages(
            session.messages,
            system_prompt if system_prompt is not None else self.system_prompt,
            preserve_recent=self.preserve_recent_messages,
        )

        if did_compact:
            session.messages = compacted
            session.usage_tracker.reset_after_compact()
            if autosave:
                session.save()
            if on_compact:
                result = on_compact(session)
                if asyncio.iscoroutine(result):
                    await result
            logger.info("Preflight compaction complete: %d messages remain", len(compacted))
            return True

        return False

    async def _maybe_auto_compact(self, session, system_prompt: str | None = None, autosave: bool = True, on_compact: Callable[[object], Awaitable[None] | None] | None = None) -> bool:
        """检查是否需要自动压缩，如果需要则执行压缩

        借鉴 Claw Code 的 maybe_auto_compact() 策略：
        当累积 input tokens 超过阈值时，压缩旧消息为摘要。
        压缩只修改 session.messages（内存），不影响 PostgreSQL 中的完整历史。
        """
        if self.auto_compact_threshold <= 0:
            return False

        if not session.usage_tracker.should_compact(self.auto_compact_threshold):
            return False

        # 检查消息数量是否足够压缩
        estimated_tokens = estimate_messages_tokens(session.messages)
        logger.info(
            "Auto-compact triggered: cumulative_input=%d, estimated_tokens=%d, messages=%d",
            session.usage_tracker.cumulative_input, estimated_tokens, len(session.messages),
        )

        # 已经压缩过的消息（带 _compacted 标记）算作已压缩部分
        # 传入完整 session.messages，compact_messages 内部会处理
        compacted, did_compact = compact_messages(
            session.messages,
            system_prompt if system_prompt is not None else self.system_prompt,
            preserve_recent=self.preserve_recent_messages,
        )

        if did_compact:
            session.messages = compacted
            # 持久化压缩后的版本
            if autosave:
                session.save()
            if on_compact:
                result = on_compact(session)
                if asyncio.iscoroutine(result):
                    await result
            # 重置 usage 计数器，避免重复触发
            session.usage_tracker.reset_after_compact()
            new_tokens = estimate_messages_tokens(compacted)
            logger.info(
                "Compaction complete: %d → %d messages, ~%d tokens saved",
                len(session.messages) + (len(compacted) - len(session.messages)),
                len(compacted),
                estimated_tokens - new_tokens,
            )
            return True

        return False
