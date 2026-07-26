from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from web.backend.db.models import Paper, UserPaper
from .base import SharedRepository


class PaperRepository(SharedRepository):
    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_id(self, paper_id: str) -> Paper | None:
        result = await self.db.execute(
            select(Paper).where(Paper.id == paper_id)
        )
        return result.scalar_one_or_none()

    async def get_visible(self, paper_id: str, user_id: UUID | None = None) -> Paper | None:
        """Return public, owned, or explicitly associated papers."""
        stmt = select(Paper).where(Paper.id == paper_id)
        if user_id:
            stmt = stmt.where(or_(
                Paper.visibility == "public",
                Paper.created_by_user_id == user_id,
                exists().where(
                    UserPaper.paper_id == Paper.id,
                    UserPaper.user_id == user_id,
                ),
            ))
        else:
            stmt = stmt.where(Paper.visibility == "public")
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(self, q: str | None = None, user_id: UUID | None = None) -> list[Paper]:
        """List public, owned, or explicitly associated papers."""
        stmt = select(Paper)
        if user_id:
            stmt = stmt.where(or_(
                Paper.visibility == "public",
                Paper.created_by_user_id == user_id,
                exists().where(
                    UserPaper.paper_id == Paper.id,
                    UserPaper.user_id == user_id,
                ),
            ))
        else:
            stmt = stmt.where(Paper.visibility == "public")
        if q:
            stmt = stmt.where(Paper.title.ilike(f"%{q}%"))
        stmt = stmt.order_by(Paper.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert(self, paper_data: dict) -> Paper:
        existing = await self.get_by_id(paper_data["id"])
        if existing:
            for key, value in paper_data.items():
                if key != "id":
                    setattr(existing, key, value)
        else:
            existing = Paper(**paper_data)
            self.db.add(existing)
        await self.db.flush()
        return existing
