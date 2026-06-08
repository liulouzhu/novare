from sqlalchemy.orm import Session
from web.backend.db.models import Paper
from .base import SharedRepository


class PaperRepository(SharedRepository):
    def __init__(self, db: Session):
        super().__init__(db)

    def get_by_id(self, paper_id: str) -> Paper | None:
        return self.db.query(Paper).filter(Paper.id == paper_id).first()

    def get_all(self, q: str | None = None) -> list[Paper]:
        query = self.db.query(Paper)
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
