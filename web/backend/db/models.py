import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Text, Boolean, DateTime, Float,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from .base import Base


def utcnow():
    return datetime.now(timezone.utc)


def gen_uuid():
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class Paper(Base):
    """Shared paper metadata — NOT user-scoped."""
    __tablename__ = "papers"

    id = Column(String(255), primary_key=True)  # doi:xxx, arxiv:xxx, s2:xxx
    title = Column(Text, nullable=False)
    authors = Column(JSONB, default=list)
    abstract = Column(Text)
    year = Column(Integer)
    source = Column(String(50))
    pdf_path = Column(Text)
    url = Column(Text)
    citation_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class UserPaper(Base):
    """Association: which user has parsed which paper."""
    __tablename__ = "user_papers"
    __table_args__ = (
        UniqueConstraint("user_id", "paper_id", name="uq_user_paper"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    paper_id = Column(String(255), ForeignKey("papers.id"), nullable=False, index=True)
    parsed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    user = relationship("User")
    paper = relationship("Paper")


class Chunk(Base):
    """Shared paper chunks — generated once per paper."""
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String(255), ForeignKey("papers.id"), nullable=False, index=True)
    section = Column(String(255))
    ordinal = Column(Integer)
    text = Column(Text, nullable=False)


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        Index("idx_citations_source", "source_id"),
        Index("idx_citations_target", "target_id"),
    )

    source_id = Column(String(255), ForeignKey("papers.id"), primary_key=True)
    target_id = Column(String(255), ForeignKey("papers.id"), primary_key=True)


class SessionModel(Base):
    """Chat session — user-scoped."""
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)  # keep existing format: timestamp-uuid
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(Text, default="New Chat")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    user = relationship("User")


class MessageModel(Base):
    """Chat message — scoped via session."""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user, assistant, tool, system
    content = Column(Text)
    tool_calls = Column(JSONB)  # assistant tool_calls array
    tool_call_id = Column(String(128))  # for tool role messages
    name = Column(String(128))  # tool name
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    type = Column(String(50))  # concept, paper, author, method, dataset, task
    properties = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        UniqueConstraint("user_id", "source_node_id", "target_node_id", "relation_type", name="uq_user_edge"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    source_node_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), nullable=False)
    target_node_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), nullable=False)
    relation_type = Column(String(100), nullable=False)
    properties = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
