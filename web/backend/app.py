"""FastAPI 应用入口 — 管理 Agent 生命周期，注册路由"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 在 import 业务模块前加载 .env，确保 DATABASE_URL 等变量可用
from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from web.backend.agent_service import AgentService  # noqa: E402
from web.backend.db.base import Base, SessionLocal, engine  # noqa: E402
from web.backend.redis_service import redis_service  # noqa: E402
from web.backend.sandbox.manager import (  # noqa: E402
    IDLE_TIMEOUT,
    sandbox_manager,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
web_logger = logging.getLogger("novare.web")

# 全局 AgentService 单例
agent_service = AgentService()


async def _idle_cleanup_loop():
    """Periodically evict sandbox containers that exceed IDLE_TIMEOUT."""
    interval = max(60, IDLE_TIMEOUT // 4)
    while True:
        await asyncio.sleep(interval)
        try:
            sandbox_manager.cleanup_idle()
        except Exception:
            web_logger.warning("Sandbox idle cleanup error", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期"""
    # 切换工作目录到项目根目录（.env 在此）
    os.chdir(PROJECT_ROOT)

    # 确保数据库表存在
    import web.backend.db.models  # noqa: F401 — register all models with Base
    Base.metadata.create_all(bind=engine)
    web_logger.info("DB tables ensured")

    # Clean up stale sandbox containers from previous runs, then start idle watcher
    sandbox_manager.startup()
    cleanup_task = asyncio.create_task(_idle_cleanup_loop())
    web_logger.info("Sandbox idle cleanup task started (interval=%ds)", max(60, IDLE_TIMEOUT // 4))

    await agent_service.initialize()

    # ── Redis 初始化（可选，失败不阻止启动） ──
    if agent_service.config:
        try:
            await redis_service.initialize(
                enabled=agent_service.config.redis_enabled,
                url=agent_service.config.redis_url,
            )
            redis_status = "disabled"
            if agent_service.config.redis_enabled:
                redis_status = "ok" if redis_service.is_available else "unavailable"
            web_logger.info(
                "Redis health: enabled=%s available=%s status=%s",
                agent_service.config.redis_enabled,
                redis_service.is_available,
                redis_status,
            )
        except Exception:
            web_logger.warning("Redis init failed (non-fatal), continuing without Redis", exc_info=True)

    # ── 多渠道接入系统 ──
    channel_tasks: list[asyncio.Task] = []
    if agent_service.config and agent_service.config.channels_enabled:
        from novare.channels.bus import MessageBus
        from novare.channels.manager import ChannelManager
        from novare.channels.adapter import AgentAdapter

        bus = MessageBus()
        manager = ChannelManager(agent_service.config.channels, bus)
        adapter = AgentAdapter(
            bus=bus,
            agent_service=agent_service,
            db_session_factory=SessionLocal,
            default_user_id=agent_service.config.channel_default_user_id or None,
        )

        channel_tasks.append(asyncio.create_task(manager.start_all()))
        channel_tasks.append(asyncio.create_task(adapter.run()))
        web_logger.info("Channel system started: %s", list(manager.channels.keys()))

    try:
        yield
    finally:
        # Shut down sandbox lifecycle: cancel cleanup, destroy all containers
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        try:
            sandbox_manager.shutdown()
        except Exception:
            web_logger.warning("Sandbox shutdown error (non-fatal)", exc_info=True)

        # Shut down channel system
        if channel_tasks:
            await adapter.stop()
            await manager.stop_all()
            for t in channel_tasks:
                t.cancel()
            for t in channel_tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # 关闭 Redis 连接
        await redis_service.close()

        try:
            await agent_service.shutdown()
        except Exception:
            web_logger.warning("Shutdown error (non-fatal)", exc_info=True)


app = FastAPI(
    title="Novare Web API",
    description="智能科研助手 Web 接口",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 开发时允许前端 dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from web.backend.auth.router import router as auth_router  # noqa: E402
from web.backend.routes.chat import router as chat_router  # noqa: E402
from web.backend.routes.graph import router as graph_router  # noqa: E402
from web.backend.routes.papers import router as papers_router  # noqa: E402
from web.backend.routes.sessions import router as sessions_router  # noqa: E402
from web.backend.routes.upload import router as upload_router  # noqa: E402
from web.backend.routes.memories import router as memories_router  # noqa: E402

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(papers_router)
app.include_router(graph_router)
app.include_router(upload_router)
app.include_router(memories_router)


@app.get("/api/health")
async def health():
    result: dict = {
        "status": "ok",
        "model": agent_service.config.model if agent_service.config else "not ready",
        "redis": {"enabled": False, "available": False, "status": "disabled"},
        "database": {"status": "ok"},
        "sandbox": {"available": sandbox_manager.client is not None},
    }
    try:
        # Redis 子检查
        if redis_service._enabled:
            result["redis"]["enabled"] = True
            result["redis"]["available"] = redis_service.is_available
            try:
                pong = await redis_service.ping()
                result["redis"]["status"] = "ok" if pong else "unavailable"
            except Exception:
                result["redis"]["status"] = "unavailable"

        # DB 子检查
        try:
            db = SessionLocal()
            try:
                from sqlalchemy import text
                db.execute(text("SELECT 1"))
            finally:
                db.close()
        except Exception:
            result["database"]["status"] = "error"
    except Exception:
        # 整个 health 不能 500
        pass
    return result


@app.get("/api/skills")
async def list_skills():
    """返回所有可用 skill 列表（从文件系统动态发现）"""
    from novare.skill import discover_skills

    skill_dirs = []
    if agent_service.config:
        skill_dirs = list(agent_service.config.skill_dirs)

    skills = discover_skills(skill_dirs)
    return [
        {"name": s.name, "description": s.description}
        for s in skills
    ]
