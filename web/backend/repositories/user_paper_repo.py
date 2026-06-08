from uuid import UUID
from sqlalchemy.orm import Session
from web.backend.db.models import UserPaper
from .base import BaseRepository


class UserPaperRepository(BaseRepository):
    def __init__(self, db: Session, user_id: UUID):
        super().__init__(db, user_id)

    def associate(self, paper_id: str) -> UserPaper:
        existing = self.db.query(UserPaper).filter(
            UserPaper.user_id == self.user_id, UserPaper.paper_id == paper_id,
        ).first()
        if existing:
            return existing
        up = UserPaper(user_id=self.user_id, paper_id=paper_id)
        self.db.add(up)
        self.db.flush()
        return up

    def get_user_papers(self) -> list[str]:
        rows = self.db.query(UserPaper.paper_id).filter(UserPaper.user_id == self.user_id).all()
        return [r[0] for r in rows]

    def has_parsed(self, paper_id: str) -> bool:
        return self.db.query(UserPaper).filter(
            UserPaper.user_id == self.user_id, UserPaper.paper_id == paper_id,
        ).first() is not None
