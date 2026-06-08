import pytest
from uuid import uuid4
from sqlalchemy import create_engine, event, types as satypes
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from web.backend.db.base import Base
from web.backend.db.models import User, Paper, SessionModel, MessageModel
from web.backend.repositories import PaperRepository, SessionRepository, MessageRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")

    # Enable foreign key support for SQLite
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Snapshot original column types so we can restore after SQLite DDL
    original_types: dict[tuple, object] = {}
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                original_types[(table.name, col.name)] = col.type
                col.type = satypes.JSON()

    Base.metadata.create_all(engine)

    # Restore original PostgreSQL types so other tests aren't affected
    for table in Base.metadata.tables.values():
        for col in table.columns:
            key = (table.name, col.name)
            if key in original_types:
                col.type = original_types[key]
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_paper_repository_upsert(db):
    repo = PaperRepository(db)
    paper = repo.upsert({"id": "doi:10.1234/test", "title": "Test Paper"})
    assert paper.id == "doi:10.1234/test"
    found = repo.get_by_id("doi:10.1234/test")
    assert found.title == "Test Paper"


def test_session_repository_user_scoped(db):
    # Create a user first (FK constraint on sessions.user_id)
    user_id = uuid4()
    user = User(id=user_id, username="testuser", email="test@example.com", password_hash="x")
    db.add(user)
    db.flush()

    repo = SessionRepository(db, user_id)
    repo.create("sess-1", "Test Session")
    sessions = repo.list_all()
    assert len(sessions) == 1
    assert sessions[0].id == "sess-1"

    # Different user should see nothing
    other_user = uuid4()
    other_user_obj = User(id=other_user, username="other", email="other@example.com", password_hash="x")
    db.add(other_user_obj)
    db.flush()
    other_repo = SessionRepository(db, other_user)
    assert len(other_repo.list_all()) == 0
