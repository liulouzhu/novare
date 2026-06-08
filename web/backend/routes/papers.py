"""论文查询端点"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from web.backend.db.base import get_db
from web.backend.db.models import User, Chunk
from web.backend.auth.dependencies import get_current_user
from web.backend.repositories import PaperRepository, UserPaperRepository
from web.backend.models import PaperOut

logger = logging.getLogger("novare.web.papers")
router = APIRouter(prefix="/api/papers", tags=["papers"])


def _paper_to_out(paper, is_parsed: bool) -> dict:
    """将 ORM Paper 对象转为 PaperOut dict"""
    authors = paper.authors if isinstance(paper.authors, list) else []
    return {
        "id": paper.id,
        "title": paper.title,
        "authors": authors,
        "abstract": paper.abstract,
        "year": paper.year,
        "source": paper.source,
        "url": paper.url,
        "pdf_path": paper.pdf_path,
        "citation_count": paper.citation_count or 0,
        "is_parsed": is_parsed,
        "created_at": paper.created_at.isoformat() if paper.created_at else None,
    }


@router.get("", response_model=list[PaperOut])
async def list_papers(
    q: str | None = Query(None, description="搜索关键词"),
    is_parsed: bool | None = Query(None, description="是否已解析"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出论文"""
    paper_repo = PaperRepository(db)
    user_paper_repo = UserPaperRepository(db, user.id)

    papers = paper_repo.get_all(q=q)

    # 获取当前用户已解析的论文 ID 集合
    parsed_ids = set(user_paper_repo.get_user_papers())

    result = []
    for p in papers[:limit]:
        parsed = p.id in parsed_ids
        if is_parsed is not None and parsed != is_parsed:
            continue
        result.append(_paper_to_out(p, parsed))

    return result


@router.get("/{paper_id:path}", response_model=PaperOut)
async def get_paper(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取论文详情"""
    paper_repo = PaperRepository(db)
    user_paper_repo = UserPaperRepository(db, user.id)

    paper = paper_repo.get_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    is_parsed = user_paper_repo.has_parsed(paper_id)
    return _paper_to_out(paper, is_parsed)


@router.get("/{paper_id:path}/chunks")
async def get_paper_chunks(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取论文的文本块"""
    paper_repo = PaperRepository(db)
    paper = paper_repo.get_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    chunks = (
        db.query(Chunk)
        .filter(Chunk.paper_id == paper_id)
        .order_by(Chunk.ordinal)
        .all()
    )
    return [
        {"id": c.id, "section": c.section, "ordinal": c.ordinal, "text": c.text}
        for c in chunks
    ]
