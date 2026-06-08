"""FastAPI 应用入口 — 管理 Agent 生命周期，注册路由"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.backend.agent_service import AgentService  # noqa: E402
from web.backend.db.base import Base, engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# 全局 AgentService 单例
agent_service = AgentService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期"""
    # 切换工作目录到项目根目录（.env 在此）
    os.chdir(PROJECT_ROOT)

    # 确保数据库表存在
    import web.backend.db.models  # noqa: F401 — register all models with Base
    Base.metadata.create_all(bind=engine)
    logging.getLogger("novare.web").info("DB tables ensured")

    await agent_service.initialize()
    try:
        yield
    finally:
        try:
            await agent_service.shutdown()
        except Exception:
            logging.getLogger("novare.web").warning("Shutdown error (non-fatal)", exc_info=True)


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

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(papers_router)
app.include_router(graph_router)
app.include_router(upload_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "model": agent_service.config.model if agent_service.config else "not ready"}
