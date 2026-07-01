from uuid import UUID
from sqlalchemy.orm import Session
from web.backend.db.models import MessageModel, SessionModel
from .base import BaseRepository


class MessageRepository(BaseRepository):
    def __init__(self, db: Session, user_id: UUID):
        super().__init__(db, user_id)

    def add_message(self, session_id: str, role: str, content: str | None = None,
                    tool_calls: list | None = None, tool_call_id: str | None = None,
                    name: str | None = None) -> MessageModel:
        msg = MessageModel(
            session_id=session_id, role=role, content=content,
            tool_calls=tool_calls, tool_call_id=tool_call_id, name=name,
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    def get_messages(self, session_id: str) -> list[MessageModel]:
        session = self.db.query(SessionModel).filter(
            SessionModel.id == session_id, SessionModel.user_id == self.user_id,
        ).first()
        if not session:
            return []
        return self.db.query(MessageModel).filter(
            MessageModel.session_id == session_id,
        ).order_by(MessageModel.id).all()

    def _verify_session_ownership(self, session_id: str) -> bool:
        """校验 session 是否属于当前用户，供所有写操作内部使用。"""
        return self.db.query(SessionModel.id).filter(
            SessionModel.id == session_id,
            SessionModel.user_id == self.user_id,
        ).first() is not None

    def delete_by_session(self, session_id: str) -> bool:
        """删除 session 下所有消息。返回 False 表示 session 不属于当前用户（拒绝操作）。"""
        if not self._verify_session_ownership(session_id):
            return False
        self.db.query(MessageModel).filter(MessageModel.session_id == session_id).delete()
        self.db.flush()
        return True

    def replace_session_messages(self, session_id: str, messages: list[dict]) -> bool:
        """删除该 session 的全部旧消息，按 messages 顺序重新插入。

        返回 False 表示 session 不属于当前用户（拒绝操作）。
        调用方负责 commit/rollback。用于 compact 后替换 DB 中的上下文消息。
        """
        if not self._verify_session_ownership(session_id):
            return False
        self.db.query(MessageModel).filter(MessageModel.session_id == session_id).delete()
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
        self.db.flush()
        return True
