from uuid import UUID
from sqlalchemy.orm import Session
from web.backend.db.models import SessionModel
from .base import BaseRepository


class SessionRepository(BaseRepository):
    def __init__(self, db: Session, user_id: UUID):
        super().__init__(db, user_id)

    def create(self, session_id: str, title: str = "New Chat") -> SessionModel:
        s = SessionModel(id=session_id, user_id=self.user_id, title=title)
        self.db.add(s)
        self.db.flush()
        return s

    def get_by_id(self, session_id: str) -> SessionModel | None:
        return self.db.query(SessionModel).filter(
            SessionModel.id == session_id, SessionModel.user_id == self.user_id,
        ).first()

    def list_all(self) -> list[SessionModel]:
        return self.db.query(SessionModel).filter(
            SessionModel.user_id == self.user_id,
        ).order_by(SessionModel.updated_at.desc()).all()

    def delete(self, session_id: str) -> bool:
        s = self.get_by_id(session_id)
        if s:
            self.db.delete(s)
            self.db.flush()
            return True
        return False

    def update_title(self, session_id: str, title: str) -> bool:
        s = self.get_by_id(session_id)
        if s:
            s.title = title
            self.db.flush()
            return True
        return False
