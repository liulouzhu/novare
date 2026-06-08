import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get(
    "POSTGRES_URL",
    "postgresql://postgres:postgres@localhost:5432/research_agent",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a DB session, auto-closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
