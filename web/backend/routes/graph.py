"""知识图谱 API 端点"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from web.backend.db.base import get_db
from web.backend.db.models import User
from web.backend.auth.dependencies import get_current_user
from web.backend.repositories import KnowledgeRepository

logger = logging.getLogger("novare.web.graph")
router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("")
async def get_graph(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取完整知识图谱数据（nodes + edges）"""
    repo = KnowledgeRepository(db, user.id)
    data = repo.get_graph_data()

    # 前端期望的节点字段（带默认值）
    nodes = []
    for n in data["nodes"]:
        nodes.append({
            "id": n.get("id", ""),
            "type": n.get("type", "Unknown"),
            "label": n.get("label", ""),
            "name": n.get("name", ""),
            "title": n.get("title", ""),
            "year": n.get("year"),
            "citation_count": n.get("citation_count", 0),
            "description": n.get("description", ""),
        })

    # 前端期望 links 中是 type 字段，仓库返回的是 relation
    links = []
    for l in data["links"]:
        links.append({
            "source": l.get("source", ""),
            "target": l.get("target", ""),
            "type": l.get("relation", ""),
        })

    return {"nodes": nodes, "links": links}


@router.get("/stats")
async def get_graph_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取图谱统计信息"""
    repo = KnowledgeRepository(db, user.id)
    return repo.get_stats()
