"""novare/channels/adapter.py — MessageBus ↔ AgentLoop 桥接层

从 MessageBus 消费 InboundMessage，调用 AgentLoop.run_turn()，
将响应通过 OutboundMessage 推回总线。

这是渠道系统的核心胶水层，实现了「平台消息 → Agent 处理 → 平台回复」的完整链路。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from novare.channels.bus import MessageBus
from novare.channels.events import InboundMessage, OutboundMessage
from novare.config import get_user_workspace
from web.backend.redis_service import redis_service

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
            # ── Redis 消息去重：防止渠道重复投递触发多次 agent 调用 ──
            if redis_service.is_available:
                dedupe_key = self._build_dedupe_key(msg)
                if dedupe_key:
                    is_new = await redis_service.set_nx(dedupe_key, "1", ttl=3600)
                    if is_new is False:
                        logger.debug("Duplicate channel message skipped: %s", dedupe_key)
                        return

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

    def _build_tool_context(self, user_id: str | None) -> dict | None:
        """构建传给 agent.run_turn 的 tool_context。

        优先使用 agent_service._workspace_for(user_id)（一致于 Web 路径），
        若该方法不可用则退回 novare.config.get_user_workspace(user_id)。
        无 user_id 时返回 None（保持兼容）。
        """
        if not user_id:
            return None
        ws_path = None
        try:
            ws_path = self.agent_service._workspace_for(user_id)
        except AttributeError:
            ws_path = None
        if ws_path is None:
            ws_path = get_user_workspace(user_id)
        return {"user_id": user_id, "workspace": str(ws_path)}

    @staticmethod
    def _build_dedupe_key(msg: InboundMessage) -> str | None:
        """构造去重 key。优先使用 metadata 中的稳定 message_id。"""
        # 优先从 metadata 取稳定的 message_id
        meta = msg.metadata
        for field in ("message_id", "msg_id", "id"):
            mid = meta.get(field)
            if mid:
                return f"dedupe:channel:{msg.channel}:{mid}"

        # 退化为 sender + session + content hash
        content_hash = hashlib.md5(msg.content.encode("utf-8")).hexdigest()[:12]
        return f"dedupe:channel:{msg.channel}:{msg.sender_id}:{msg.session_key}:{content_hash}"

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

        ctx = self._build_tool_context(user_id)
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

        ctx = self._build_tool_context(user_id)
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
        2. 查询 channel_users 映射表，找到已有映射则返回。
        3. 没有映射则自动注册：创建 Novare User + ChannelUser 映射。
        """
        if self.default_user_id:
            return self.default_user_id

        if not self.db_session_factory:
            return None

        try:
            from uuid import UUID
            from web.backend.db.models import ChannelUser, User
            from web.backend.db.base import SessionLocal

            db = SessionLocal()
            try:
                # 查询已有映射
                mapping = db.query(ChannelUser).filter(
                    ChannelUser.channel == channel,
                    ChannelUser.platform_user_id == sender_id,
                ).first()

                if mapping:
                    return str(mapping.novare_user_id)

                # 自动注册新用户
                user_id = self._auto_register_user(db, sender_id, channel)
                db.commit()
                return user_id
            except Exception:
                db.rollback()
                logger.exception("Failed to resolve channel user")
                return None
            finally:
                db.close()
        except Exception:
            logger.exception("DB connection failed in _resolve_user")
            return None

    @staticmethod
    def _auto_register_user(db: Any, sender_id: str, channel: str) -> str:
        """为渠道用户自动创建 Novare 用户和映射记录。"""
        import hashlib
        import secrets
        from web.backend.db.models import ChannelUser, User

        # 生成唯一用户名和邮箱
        sender_hash = hashlib.md5(f"{channel}:{sender_id}".encode()).hexdigest()[:10]
        username = f"{channel}_{sender_hash}"
        email = f"{channel}_{sender_hash}@channel.novare.local"
        # 随机密码哈希，该用户不能通过 Web 登录，只能通过渠道使用
        password_hash = secrets.token_hex(32)

        # 创建 Novare 用户
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
        )
        db.add(user)
        db.flush()  # 获取 user.id

        # 创建渠道映射
        mapping = ChannelUser(
            novare_user_id=user.id,
            channel=channel,
            platform_user_id=sender_id,
        )
        db.add(mapping)
        db.flush()

        logger.info("Auto-registered channel user: %s:%s -> %s (%s)", channel, sender_id, user.id, username)
        return str(user.id)

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
                if not msg_repo.replace_session_messages(session.session_id, session.messages):
                    logger.warning("replace_session_messages rejected: session %s not owned by user %s", session.session_id, user_id)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Failed to persist channel session to DB")
            finally:
                db.close()
        except Exception:
            logger.exception("DB session creation failed")
