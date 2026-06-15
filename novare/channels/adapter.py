"""novare/channels/adapter.py — MessageBus ↔ AgentLoop 桥接层

从 MessageBus 消费 InboundMessage，调用 AgentLoop.run_turn()，
将响应通过 OutboundMessage 推回总线。

这是渠道系统的核心胶水层，实现了「平台消息 → Agent 处理 → 平台回复」的完整链路。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from novare.channels.bus import MessageBus
from novare.channels.events import InboundMessage, OutboundMessage

logger = logging.getLogger("novare.channels.adapter")


class AgentAdapter:
    """桥接 MessageBus 和 AgentLoop。

    持续消费入站消息，为每条消息创建/加载 session，
    调用 AgentLoop.run_turn()，并将响应推送到出站队列。

    对于不支持流式输出的渠道（如微信），缓存全部 delta 后一次性发送。
    对于支持流式输出的渠道，逐 chunk 转发。
    """

    def __init__(
        self,
        bus: MessageBus,
        agent_service: Any,  # web.backend.agent_service.AgentService
        db_session_factory: Any = None,  # web.backend.db.base.SessionLocal
        default_user_id: str | None = None,
    ):
        """
        Args:
            bus: 共享的消息总线。
            agent_service: AgentService 实例，提供 agent_loop / load_session / config 等。
            db_session_factory: 可选的 DB session 工厂（SessionLocal）。
            default_user_id: 默认用户 ID，用于未注册渠道用户的匿名访问。
        """
        self.bus = bus
        self.agent_service = agent_service
        self.db_session_factory = db_session_factory
        self.default_user_id = default_user_id
        self._running = False

    async def run(self) -> None:
        """主循环：持续消费入站消息并处理。"""
        self._running = True
        logger.info("AgentAdapter started, waiting for inbound messages...")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=5.0,
                )
                # 每条消息独立 task 处理，避免单条阻塞后续消息
                asyncio.create_task(self._handle_one(msg))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("AgentAdapter main loop error")

    async def stop(self) -> None:
        self._running = False

    async def _handle_one(self, msg: InboundMessage) -> None:
        """处理单条入站消息。"""
        try:
            # 1. 解析用户和会话
            user_id = await self._resolve_user(msg.sender_id, msg.channel)
            session = self.agent_service.load_session(
                session_id=msg.session_key,
                user_id=user_id,
            )

            # 2. 判断渠道是否支持流式输出
            channel_supports_stream = msg.metadata.get("_wants_stream", False)

            if channel_supports_stream:
                # 流式模式：逐 chunk 转发
                await self._run_with_streaming(msg, session, user_id)
            else:
                # 非流式模式（微信等）：缓存后一次性发送
                await self._run_with_buffering(msg, session, user_id)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to handle message from %s:%s", msg.channel, msg.sender_id)
            # 尝试发送错误提示
            try:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⚠️ 处理消息时出错，请稍后重试。",
                ))
            except Exception:
                pass

    async def _run_with_streaming(
        self,
        msg: InboundMessage,
        session: Any,
        user_id: str | None,
    ) -> None:
        """流式模式：逐 chunk 发送 _stream_delta。"""
        def on_text(delta: str):
            self.bus.outbound.put_nowait(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=delta,
                metadata={"_stream_delta": True},
            ))

        def on_tool(event: str, name: str, args: dict, result: str | None, duration: float | None):
            if event == "start":
                self.bus.outbound.put_nowait(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"🔧 {name}...",
                    metadata={"_progress": True, "_tool_hint": True},
                ))

        ctx = {"user_id": user_id} if user_id else None
        await self.agent_service.agent.run_turn(
            session,
            msg.content,
            on_text=on_text,
            on_tool=on_tool,
            tool_context=ctx,
            system_prompt=self.agent_service.config.system_prompt,
            autosave=False,
        )

        # 发送流结束标记
        self.bus.outbound.put_nowait(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="",
            metadata={"_stream_delta": True, "_stream_end": True, "_streamed": True},
        ))

        # 持久化（DB 模式）
        await self._persist_session(session, user_id)

    async def _run_with_buffering(
        self,
        msg: InboundMessage,
        session: Any,
        user_id: str | None,
    ) -> None:
        """非流式模式（微信等）：缓存全部输出后一次性发送。"""
        final_parts: list[str] = []

        def on_text(delta: str):
            final_parts.append(delta)

        def on_tool(event: str, name: str, args: dict, result: str | None, duration: float | None):
            # 非流式模式下，可选发送工具调用提示
            pass

        ctx = {"user_id": user_id} if user_id else None
        await self.agent_service.agent.run_turn(
            session,
            msg.content,
            on_text=on_text,
            on_tool=on_tool,
            tool_context=ctx,
            system_prompt=self.agent_service.config.system_prompt,
            autosave=False,
        )

        # 一次性发送完整响应
        final_text = "".join(final_parts).strip()
        if final_text:
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=final_text,
            ))

        # 持久化
        await self._persist_session(session, user_id)

    async def _resolve_user(self, sender_id: str, channel: str) -> str | None:
        """将渠道 sender_id 映射到 Novare user_id。

        策略：
        1. 如果配置了 default_user_id，直接使用（单用户场景）。
        2. 否则尝试从数据库查询 channel_users 映射表。
        3. 都没有则返回 None（使用默认 workspace）。
        """
        if self.default_user_id:
            return self.default_user_id

        # TODO: 查询 channel_users 映射表（首次自动注册）
        # 暂时返回 None，使用默认 workspace
        return None

    async def _persist_session(self, session: Any, user_id: str | None) -> None:
        """持久化会话消息到 DB。"""
        if not user_id or not self.db_session_factory:
            return
        try:
            db = self.db_session_factory()
            try:
                from web.backend.repositories import SessionRepository, MessageRepository
                from uuid import UUID

                user_uuid = UUID(user_id)
                session_repo = SessionRepository(db, user_uuid)
                session_model = session_repo.get_by_id(session.session_id)
                if not session_model:
                    session_repo.create(session.session_id, title="微信会话")

                msg_repo = MessageRepository(db, user_uuid)
                # 增量追加（简单实现：全量替换，后续优化为增量）
                msg_repo.replace_session_messages(session.session_id, session.messages)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Failed to persist channel session to DB")
            finally:
                db.close()
        except Exception:
            logger.exception("DB session creation failed")
