"""知识图谱 API 端点"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from web.backend.app import agent_service

logger = logging.getLogger("novare.web.graph")
router = APIRouter(prefix="/api/graph", tags=["graph"])


def _get_graph_path() -> Path:
    """获取知识图谱 JSON 文件路径"""
    if agent_service.config:
        return agent_service.config.data_dir / "knowledge_graph.json"
    return Path("./data/knowledge_graph.json")


@router.get("")
async def get_graph():
    """获取完整知识图谱数据（nodes + edges）"""
    graph_path = _get_graph_path()
    if not graph_path.exists():
        return {"nodes": [], "links": []}

    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load graph: {e}")

    # 转为前端友好的格式
    nodes = []
    for node in data.get("nodes", []):
        nodes.append({
            "id": node.get("id", ""),
            "type": node.get("type", "Unknown"),
            "label": node.get("name") or node.get("title") or node.get("id", ""),
            "name": node.get("name", ""),
            "title": node.get("title", ""),
            "year": node.get("year"),
            "citation_count": node.get("citation_count", 0),
            "description": node.get("description", ""),
        })

    links = []
    for edge in data.get("edges", []):
        links.append({
            "source": edge.get("source", ""),
            "target": edge.get("target", ""),
            "type": edge.get("type", ""),
        })

    return {"nodes": nodes, "links": links}


@router.get("/stats")
async def get_graph_stats():
    """获取图谱统计信息"""
    graph_path = _get_graph_path()
    if not graph_path.exists():
        return {"total_nodes": 0, "total_edges": 0, "node_types": {}, "edge_types": {}}

    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load graph: {e}")

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    node_types = {}
    for n in nodes:
        t = n.get("type", "Unknown")
        node_types[t] = node_types.get(t, 0) + 1

    edge_types = {}
    for e in edges:
        t = e.get("type", "Unknown")
        edge_types[t] = edge_types.get(t, 0) + 1

    return {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "node_types": node_types,
        "edge_types": edge_types,
    }
