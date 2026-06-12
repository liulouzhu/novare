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
    exclude: str = "Author",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取完整知识图谱数据（nodes + edges）

    Query params:
        exclude: 逗号分隔的节点类型列表，默认 "Author"。
                 传空字符串 exclude= 可返回所有节点。
    """
    repo = KnowledgeRepository(db, user.id)
    exclude_types = [t.strip() for t in exclude.split(",") if t.strip()] if exclude else []
    data = repo.get_graph_data(exclude_types=exclude_types)

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
            "canonical_name": n.get("canonical_name", ""),
            "aliases": n.get("aliases", []),
            "source_mentions": n.get("source_mentions", []),
        })

    # 前端期望 links 中是 type 字段，仓库返回的是 relation
    links = []
    for l in data["links"]:
        links.append({
            "source": l.get("source", ""),
            "target": l.get("target", ""),
            "type": l.get("relation", ""),
            "alternate_relations": l.get("alternate_relations", []),
            "confidence": l.get("confidence"),
            "inference": l.get("inference", ""),
            "shared_tasks": l.get("shared_tasks", []),
            "shared_datasets": l.get("shared_datasets", []),
            "shared_methods": l.get("shared_methods", []),
            "shared_metrics": l.get("shared_metrics", []),
            "evidence_note": l.get("evidence_note", ""),
        })

    return {"nodes": nodes, "links": links}


@router.get("/stats")
async def get_graph_stats(
    exclude: str = "Author",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取图谱统计信息"""
    repo = KnowledgeRepository(db, user.id)
    exclude_types = [t.strip() for t in exclude.split(",") if t.strip()] if exclude else []
    return repo.get_stats(exclude_types=exclude_types)
