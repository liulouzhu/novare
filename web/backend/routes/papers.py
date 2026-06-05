"""论文查询端点"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from web.backend.app import agent_service
from web.backend.models import PaperOut

logger = logging.getLogger("novare.web.papers")
router = APIRouter(prefix="/api/papers", tags=["papers"])


def _get_db_path() -> Path:
    """获取 SQLite 数据库路径"""
    if agent_service.config:
        return agent_service.config.data_dir / "research.db"
    return Path("./data/research.db")


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接"""
    db_path = _get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_paper(row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
    """将数据库行转为 PaperOut dict"""
    authors = []
    try:
        authors = json.loads(row["authors"]) if row["authors"] else []
    except (json.JSONDecodeError, TypeError):
        pass

    # 检查是否已解析（有 chunks）
    has_chunks = conn.execute(
        "SELECT 1 FROM chunks WHERE paper_id = ? LIMIT 1", (row["id"],)
    ).fetchone()

    return {
        "id": row["id"],
        "title": row["title"],
        "authors": authors,
        "abstract": row["abstract"],
        "year": row["year"],
        "source": row["source"],
        "url": row["url"],
        "pdf_path": row["pdf_path"],
        "citation_count": row["citation_count"] or 0,
        "is_parsed": has_chunks is not None,
        "created_at": row["created_at"],
    }


@router.get("", response_model=list[PaperOut])
async def list_papers(
    q: str | None = Query(None, description="搜索关键词"),
    is_parsed: bool | None = Query(None, description="是否已解析"),
    limit: int = Query(50, ge=1, le=200),
):
    """列出论文"""
    conn = _get_conn()
    try:
        if q:
            rows = conn.execute(
                "SELECT * FROM papers WHERE title LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM papers ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        papers = [_row_to_paper(r, conn) for r in rows]

        if is_parsed is not None:
            papers = [p for p in papers if p["is_parsed"] == is_parsed]

        return papers
    finally:
        conn.close()


@router.get("/{paper_id:path}", response_model=PaperOut)
async def get_paper(paper_id: str):
    """获取论文详情"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Paper not found")
        return _row_to_paper(row, conn)
    finally:
        conn.close()


@router.get("/{paper_id:path}/chunks")
async def get_paper_chunks(paper_id: str):
    """获取论文的文本块"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, section, ordinal, text FROM chunks WHERE paper_id = ? ORDER BY ordinal",
            (paper_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
