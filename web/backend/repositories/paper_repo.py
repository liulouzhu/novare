from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from web.backend.db.models import Paper
from .base import SharedRepository


class PaperRepository(SharedRepository):
    def __init__(self, db: Session):
        super().__init__(db)

    def get_by_id(self, paper_id: str) -> Paper | None:
        return self.db.query(Paper).filter(Paper.id == paper_id).first()

    def get_visible(self, paper_id: str, user_id: UUID | None = None) -> Paper | None:
        """获取论文，仅返回当前用户有权访问的（public 或自己创建的 private）。"""
        q = self.db.query(Paper).filter(Paper.id == paper_id)
        if user_id:
            q = q.filter(or_(Paper.visibility == "public", Paper.created_by_user_id == user_id))
        else:
            q = q.filter(Paper.visibility == "public")
        return q.first()

    def get_all(self, q: str | None = None, user_id: UUID | None = None) -> list[Paper]:
        """列出论文：public 全部可见，private 仅创建者可见。"""
        query = self.db.query(Paper)
        if user_id:
            query = query.filter(or_(Paper.visibility == "public", Paper.created_by_user_id == user_id))
        else:
            query = query.filter(Paper.visibility == "public")
        if q:
            query = query.filter(Paper.title.ilike(f"%{q}%"))
        return query.order_by(Paper.created_at.desc()).all()

    def upsert(self, paper_data: dict) -> Paper:
        paper = self.db.query(Paper).filter(Paper.id == paper_data["id"]).first()
        if paper:
            for key, value in paper_data.items():
                if key != "id":
                    setattr(paper, key, value)
        else:
            paper = Paper(**paper_data)
            self.db.add(paper)
        self.db.flush()
        return paper
