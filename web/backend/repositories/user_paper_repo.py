from uuid import UUID

from sqlalchemy.orm import Session

from web.backend.db.models import UserPaper
from .base import BaseRepository


class UserPaperRepository(BaseRepository):
    def __init__(self, db: Session, user_id: UUID):
        super().__init__(db, user_id)

    def associate(
        self,
        paper_id: str,
        relation_type: str = "searched",
        has_fulltext_access: bool = False,
        source: str | None = None,
    ) -> UserPaper:
        existing = self.db.query(UserPaper).filter(
            UserPaper.user_id == self.user_id,
            UserPaper.paper_id == paper_id,
        ).first()
        if existing:
            # 升级：parsed 覆盖 searched，fulltext 只升不降
            if existing.relation_type == "searched" and relation_type != "searched":
                existing.relation_type = relation_type
            if has_fulltext_access and not existing.has_fulltext_access:
                existing.has_fulltext_access = True
            if source:
                existing.source = source
            return existing
        up = UserPaper(
            user_id=self.user_id,
            paper_id=paper_id,
            relation_type=relation_type,
            has_fulltext_access=has_fulltext_access,
            source=source,
        )
        self.db.add(up)
        self.db.flush()
        return up

    def get_user_papers(self) -> list[str]:
        """返回用户关联的所有 paper_id（不限 relation_type）。"""
        rows = self.db.query(UserPaper.paper_id).filter(
            UserPaper.user_id == self.user_id
        ).all()
        return [r[0] for r in rows]

    def get_fulltext_paper_ids(self) -> set[str]:
        """返回用户有全文访问权限的 paper_id 集合。"""
        rows = self.db.query(UserPaper.paper_id).filter(
            UserPaper.user_id == self.user_id,
            UserPaper.has_fulltext_access.is_(True),
        ).all()
        return {r[0] for r in rows}

    def has_parsed(self, paper_id: str) -> bool:
        """向后兼容：是否有关联记录（任意类型）。"""
        return self.db.query(UserPaper).filter(
            UserPaper.user_id == self.user_id,
            UserPaper.paper_id == paper_id,
        ).first() is not None

    def has_fulltext_access(self, paper_id: str) -> bool:
        """是否有全文访问权限（parsed / uploaded / shared）。"""
        row = self.db.query(UserPaper).filter(
            UserPaper.user_id == self.user_id,
            UserPaper.paper_id == paper_id,
        ).first()
        return row is not None and row.has_fulltext_access

    def dissociate(self, paper_id: str) -> bool:
        """移除当前用户与论文的关联，返回是否确实删除了记录。"""
        deleted = self.db.query(UserPaper).filter(
            UserPaper.user_id == self.user_id,
            UserPaper.paper_id == paper_id,
        ).delete()
        return deleted > 0

    def count_associations(self, paper_id: str) -> int:
        """统计有多少用户关联了该论文。"""
        return self.db.query(UserPaper).filter(
            UserPaper.paper_id == paper_id,
        ).count()
