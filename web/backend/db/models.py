import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Text, Boolean, DateTime, Float, LargeBinary,
    ForeignKey, UniqueConstraint, Index, CheckConstraint, JSON,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import relationship

from .base import Base


# JSON 类型：PostgreSQL 使用 JSONB，SQLite/其他使用 JSON
JSON_TYPE = JSON().with_variant(PG_JSONB(), "postgresql")


def utcnow():
    return datetime.now(timezone.utc)


def gen_uuid():
    return uuid.uuid4()


class GUID(TypeDecorator):
    """UUID 类型适配器：PostgreSQL 用原生 UUID，SQLite 用 CHAR(36)。"""
    impl = String
    cache_ok = True

    def __init__(self):
        super().__init__(length=36)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return parsed  # asyncpg 接受 uuid.UUID
        return str(parsed)  # SQLite 需要字符串

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))


class User(Base):
    __tablename__ = "users"

    id = Column(GUID(), primary_key=True, default=gen_uuid)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class Paper(Base):
    """Shared paper metadata — public papers visible to all, private papers owner-only."""
    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint("visibility IN ('public', 'private')", name="ck_paper_visibility"),
    )

    id = Column(String(255), primary_key=True)  # doi:xxx, arxiv:xxx, s2:xxx
    title = Column(Text, nullable=False)
    authors = Column(JSON_TYPE, default=list)
    abstract = Column(Text)
    year = Column(Integer)
    source = Column(String(50))
    pdf_path = Column(Text)
    url = Column(Text)
    citation_count = Column(Integer, default=0)
    visibility = Column(String(10), nullable=False, default="public", server_default="public")
    created_by_user_id = Column(GUID(), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class UserPaper(Base):
    """User-paper relationship with typed access levels."""
    __tablename__ = "user_papers"
    __table_args__ = (
        UniqueConstraint("user_id", "paper_id", name="uq_user_paper"),
        CheckConstraint(
            "relation_type IN ('searched', 'parsed', 'uploaded', 'shared')",
            name="ck_user_paper_relation_type",
        ),
    )

    id = Column(GUID(), primary_key=True, default=gen_uuid)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    paper_id = Column(String(255), ForeignKey("papers.id"), nullable=False, index=True)
    relation_type = Column(String(20), nullable=False, default="searched", server_default="searched")
    has_fulltext_access = Column(Boolean, nullable=False, default=False, server_default="false")
    source = Column(String(30))  # paper_search | paper_parse | upload | share
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


class Embedding(Base):
    """Vector embedding for a chunk — stored as numpy float32 bytes."""
    __tablename__ = "embeddings"

    chunk_id = Column(Integer, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    dim = Column(Integer, nullable=False)
    vec = Column(LargeBinary, nullable=False)  # numpy.float32 tobytes()


class SessionModel(Base):
    """Chat session — user-scoped."""
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)  # keep existing format: timestamp-uuid
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
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
    tool_calls = Column(JSON_TYPE)  # assistant tool_calls array
    tool_call_id = Column(String(128))  # for tool role messages
    name = Column(String(128))  # tool name
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id = Column(GUID(), primary_key=True, default=gen_uuid)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    type = Column(String(50))  # concept, paper, author, method, dataset, task
    properties = Column(JSON_TYPE, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        UniqueConstraint("user_id", "source_node_id", "target_node_id", "relation_type", name="uq_user_edge"),
    )

    id = Column(GUID(), primary_key=True, default=gen_uuid)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    source_node_id = Column(GUID(), ForeignKey("knowledge_nodes.id"), nullable=False)
    target_node_id = Column(GUID(), ForeignKey("knowledge_nodes.id"), nullable=False)
    relation_type = Column(String(100), nullable=False)
    properties = Column(JSON_TYPE, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ChannelUser(Base):
    """渠道用户映射 — 将平台 sender_id 关联到 Novare user_id。"""
    __tablename__ = "channel_users"
    __table_args__ = (
        UniqueConstraint("channel", "platform_user_id", name="uq_channel_user"),
    )

    id = Column(GUID(), primary_key=True, default=gen_uuid)
    novare_user_id = Column(GUID(), ForeignKey("users.id"), nullable=False, index=True)
    channel = Column(String(32), nullable=False)           # weixin, telegram, ...
    platform_user_id = Column(String(128), nullable=False)  # 平台用户 ID
    platform_username = Column(String(128))                 # 平台昵称（可选）
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User")


class UserMemory(Base):
    """用户长期记忆 — 自动从对话中提取的用户偏好。"""
    __tablename__ = "user_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "category", "key", name="uq_user_memory_key"),
        Index("idx_user_memories_user", "user_id"),
        Index("idx_user_memories_category", "user_id", "category"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    category = Column(String(50), nullable=False)    # "research_preference" | "interaction_preference"
    key = Column(String(100), nullable=False)         # 如 "research_field", "preferred_language"
    value = Column(Text, nullable=False)              # 具体值
    confidence = Column(Float, default=1.0)           # 置信度 0-1
    pinned = Column(Boolean, default=False)           # 锁定：pinned 的记忆不参与淘汰
    tags = Column(JSON_TYPE, default=list)                # 标签列表
    source = Column(String(50), default="auto")       # "auto" | "user"
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")


class EpisodicMemory(Base):
    """情景记忆 — 用户完成过的研究任务、决策、实验结果等。"""
    __tablename__ = "episodic_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", name="uq_episodic_memory_hash"),
        Index("idx_episodic_memories_user", "user_id"),
        Index("idx_episodic_memories_session", "session_id"),
        Index("idx_episodic_memories_status", "user_id", "status"),
    )

    id = Column(GUID(), primary_key=True, default=gen_uuid)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    session_id = Column(String(64), nullable=True)

    memory_type = Column(String(50), nullable=False)
    summary = Column(String(500), nullable=False)
    context = Column(Text, default="")
    action = Column(Text, default="")
    outcome = Column(Text, default="")

    topics = Column(JSON_TYPE, default=list)
    source_message_ids = Column(JSON_TYPE, default=list)

    importance = Column(Float, default=0.5)
    confidence = Column(Float, default=0.5)

    content_hash = Column(String(64), nullable=False)
    embedding_model = Column(String(100), default="")
    vector_id = Column(String(64), nullable=True)
    index_status = Column(String(20), nullable=False, default="pending")

    status = Column(String(20), nullable=False, default="active")
    pinned = Column(Boolean, default=False)

    occurred_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    last_retrieved_at = Column(DateTime(timezone=True), nullable=True)
    retrieval_count = Column(Integer, default=0)

    user = relationship("User")
