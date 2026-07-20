from uuid import UUID
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from web.backend.db.models import MessageModel, SessionModel
from .base import BaseRepository


class MessageRepository(BaseRepository):
    def __init__(self, db: AsyncSession, user_id: UUID):
        super().__init__(db, user_id)

    async def add_message(self, session_id: str, role: str, content: str | None = None,
                    tool_calls: list | None = None, tool_call_id: str | None = None,
                    name: str | None = None) -> MessageModel:
        msg = MessageModel(
            session_id=session_id, role=role, content=content,
            tool_calls=tool_calls, tool_call_id=tool_call_id, name=name,
        )
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def get_messages(self, session_id: str) -> list[MessageModel]:
        # 先验证 session 归属
        result = await self.db.execute(
            select(SessionModel.id).where(
                SessionModel.id == session_id,
                SessionModel.user_id == self.user_id,
            )
        )
        if result.scalar_one_or_none() is None:
            return []

        result = await self.db.execute(
            select(MessageModel)
            .where(MessageModel.session_id == session_id)
            .order_by(MessageModel.id)
        )
        return list(result.scalars().all())

    async def _verify_session_ownership(self, session_id: str) -> bool:
        """校验 session 是否属于当前用户，供所有写操作内部使用。"""
        result = await self.db.execute(
            select(SessionModel.id).where(
                SessionModel.id == session_id,
                SessionModel.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def delete_by_session(self, session_id: str) -> bool:
        """删除 session 下所有消息。返回 False 表示 session 不属于当前用户（拒绝操作）。"""
        if not await self._verify_session_ownership(session_id):
            return False
        await self.db.execute(
            delete(MessageModel).where(MessageModel.session_id == session_id)
        )
        await self.db.flush()
        return True

    async def replace_session_messages(self, session_id: str, messages: list[dict]) -> bool:
        """删除该 session 的全部旧消息，按 messages 顺序重新插入。

        返回 False 表示 session 不属于当前用户（拒绝操作）。
        调用方负责 commit/rollback。用于 compact 后替换 DB 中的上下文消息。
        """
        if not await self._verify_session_ownership(session_id):
            return False
        await self.db.execute(
            delete(MessageModel).where(MessageModel.session_id == session_id)
        )
        for msg in messages:
            new_msg = MessageModel(
                session_id=session_id,
                role=msg["role"],
                content=msg.get("content"),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                name=msg.get("name"),
            )
            self.db.add(new_msg)
        await self.db.flush()
        return True
