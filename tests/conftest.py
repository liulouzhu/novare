"""共享测试 fixtures — 异步 SQLite 数据库 + FastAPI 测试客户端"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# mcp-server 目录
MCP_ROOT = PROJECT_ROOT / "mcp-server"
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

# 在导入业务模块前设置测试数据库 URL
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

# 精确 Mock Milvus：只 mock 连接相关 API，不替换整个模块
import unittest.mock as _mock
from sqlalchemy import event as _sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession  # noqa: E402
from web.backend.db.base import Base  # noqa: E402


def _enable_sqlite_foreign_keys(dbapi_conn, connection_record):
    """在 SQLite 连接上启用外键约束。"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def tmp_workspace(tmp_path):
    """创建临时工作空间"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".novare").mkdir()
    return ws


@pytest.fixture
def tmp_data_dir(tmp_path):
    """创建临时数据目录"""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest_asyncio.fixture
async def db_engine():
    """创建异步 SQLite 测试引擎，启用外键约束。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    # 启用 SQLite 外键约束
    _sa_event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """创建异步测试 Session，每个测试自动回滚"""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def db_session_factory(db_engine):
    """返回 async_sessionmaker 工厂，供需要创建短生命周期 Session 的测试使用"""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def test_user(db_session):
    """创建一个测试用户"""
    from web.backend.db.models import User
    from web.backend.auth.service import hash_password
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"testuser_{user_id.hex[:8]}",
        email=f"test_{user_id.hex[:8]}@test.com",
        password_hash=hash_password("testpassword"),
    )
    db_session.add(user)
    await db_session.flush()
    return user
