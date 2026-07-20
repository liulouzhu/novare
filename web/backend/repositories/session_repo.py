from uuid import UUID
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from web.backend.db.models import SessionModel
from .base import BaseRepository


class SessionRepository(BaseRepository):
    def __init__(self, db: AsyncSession, user_id: UUID):
        super().__init__(db, user_id)

    async def create(self, session_id: str, title: str = "New Chat") -> SessionModel:
        s = SessionModel(id=session_id, user_id=self.user_id, title=title)
        self.db.add(s)
        await self.db.flush()
        return s

    async def get_by_id(self, session_id: str) -> SessionModel | None:
        result = await self.db.execute(
            select(SessionModel).where(
                SessionModel.id == session_id,
                SessionModel.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[SessionModel]:
        result = await self.db.execute(
            select(SessionModel)
            .where(SessionModel.user_id == self.user_id)
            .order_by(SessionModel.updated_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, session_id: str) -> bool:
        s = await self.get_by_id(session_id)
        if s:
            await self.db.delete(s)
            await self.db.flush()
            return True
        return False

    async def update_title(self, session_id: str, title: str) -> bool:
        s = await self.get_by_id(session_id)
        if s:
            s.title = title
            await self.db.flush()
            return True
        return False
