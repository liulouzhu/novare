"""用户长期记忆 Repository — CRUD 操作"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from web.backend.db.models import UserMemory
from .base import BaseRepository


class MemoryRepository(BaseRepository):
    def __init__(self, db: Session, user_id: UUID):
        super().__init__(db, user_id)

    def get_all(self) -> list[UserMemory]:
        """获取该用户的所有记忆条目"""
        return (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == self.user_id)
            .order_by(UserMemory.category, UserMemory.key)
            .all()
        )

    def get_by_category(self, category: str) -> list[UserMemory]:
        """按类别查询记忆"""
        return (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == self.user_id, UserMemory.category == category)
            .order_by(UserMemory.key)
            .all()
        )

    def get_by_key(self, category: str, key: str) -> UserMemory | None:
        """查询单条记忆"""
        return (
            self.db.query(UserMemory)
            .filter(
                UserMemory.user_id == self.user_id,
                UserMemory.category == category,
                UserMemory.key == key,
            )
            .first()
        )

    def upsert(
        self,
        category: str,
        key: str,
        value: str,
        confidence: float = 1.0,
        tags: list[str] | None = None,
        source: str = "auto",
    ) -> UserMemory:
        """插入或更新记忆条目

        如果相同 user_id + category + key 已存在，则更新 value、confidence、tags。
        """
        existing = self.get_by_key(category, key)
        if existing:
            existing.value = value
            existing.confidence = confidence
            if tags is not None:
                existing.tags = tags
            existing.source = source
            self.db.flush()
            return existing

        memory = UserMemory(
            user_id=self.user_id,
            category=category,
            key=key,
            value=value,
            confidence=confidence,
            tags=tags or [],
            source=source,
        )
        self.db.add(memory)
        self.db.flush()
        return memory

    def delete(self, memory_id: int) -> bool:
        """删除单条记忆"""
        memory = (
            self.db.query(UserMemory)
            .filter(UserMemory.id == memory_id, UserMemory.user_id == self.user_id)
            .first()
        )
        if memory:
            self.db.delete(memory)
            self.db.flush()
            return True
        return False

    def delete_all(self) -> int:
        """删除该用户的所有记忆，返回删除数量"""
        count = (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == self.user_id)
            .delete()
        )
        self.db.flush()
        return count

    def count(self) -> int:
        """统计该用户的记忆条目数"""
        return (
            self.db.query(UserMemory)
            .filter(UserMemory.user_id == self.user_id)
            .count()
        )
