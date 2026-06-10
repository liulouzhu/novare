"""论文查询端点"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from web.backend.db.base import get_db
from web.backend.db.models import User, Chunk
from web.backend.auth.dependencies import get_current_user
from web.backend.auth.service import decode_access_token
from web.backend.repositories import PaperRepository, UserPaperRepository
from web.backend.models import PaperOut

logger = logging.getLogger("novare.web.papers")
router = APIRouter(prefix="/api/papers", tags=["papers"])

_optional_bearer = HTTPBearer(auto_error=False)


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


def _try_get_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """可选认证：有 token 则解析，没有则返回 None。"""
    if credentials is None:
        return None
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        return None
    return db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()


# ── 无认证端点（放在 {paper_id:path} 之前，避免路径被吞） ──


@router.get("/pdf/view")
async def get_paper_pdf(
    paper_id: str = Query(..., description="论文 ID"),
    user: User | None = Depends(_try_get_user),
    db: Session = Depends(get_db),
):
    """获取论文 PDF

    外部重定向（arxiv 等公开资源）无需认证。
    本地已下载的 PDF 需要认证 + 所有权校验，防止越权读取用户私有文件。
    """
    paper_repo = PaperRepository(db)
    paper = paper_repo.get_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    # 1. 本地已下载的 PDF — 需要认证 + 所有权
    if paper.pdf_path and os.path.isfile(paper.pdf_path):
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required to access local PDF")
        user_paper_repo = UserPaperRepository(db, user.id)
        is_owner = (
            paper.visibility == "public"
            or paper.created_by_user_id == user.id
            or user_paper_repo.has_fulltext_access(paper_id)
        )
        if not is_owner:
            raise HTTPException(status_code=403, detail="You do not have access to this paper's PDF")
        return FileResponse(
            paper.pdf_path,
            media_type="application/pdf",
            filename=f"{paper.title[:80]}.pdf",
        )

    # 2. 外部重定向（公开资源，无需认证）
    pid = paper.id
    if pid.startswith("arxiv:"):
        arxiv_id = pid.removeprefix("arxiv:")
        return RedirectResponse(url=f"https://arxiv.org/pdf/{arxiv_id}", status_code=302)

    if pid.startswith("doi:10.48550/arXiv."):
        arxiv_id = pid.removeprefix("doi:10.48550/arXiv.")
        return RedirectResponse(url=f"https://arxiv.org/pdf/{arxiv_id}", status_code=302)

    if paper.url:
        return RedirectResponse(url=paper.url, status_code=302)

    raise HTTPException(status_code=404, detail="PDF not available")


# ── 需要认证的端点 ──


@router.get("", response_model=list[PaperOut])
async def list_papers(
    q: str | None = Query(None, description="搜索关键词"),
    is_parsed: bool | None = Query(None, description="是否已解析"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户的论文（搜索 + 解析过的）"""
    paper_repo = PaperRepository(db)
    user_paper_repo = UserPaperRepository(db, user.id)

    # 只返回该用户关联的论文（搜索或解析时自动创建 UserPaper）
    user_paper_ids = set(user_paper_repo.get_user_papers())
    if not user_paper_ids:
        return []

    fulltext_ids = user_paper_repo.get_fulltext_paper_ids()
    papers = paper_repo.get_all(q=q, user_id=user.id)

    result = []
    for p in papers:
        if p.id not in user_paper_ids:
            continue
        parsed = p.id in fulltext_ids
        if is_parsed is not None and parsed != is_parsed:
            continue
        result.append(_paper_to_out(p, parsed))
        if len(result) >= limit:
            break

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

    paper = paper_repo.get_visible(paper_id, user.id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    is_parsed = user_paper_repo.has_fulltext_access(paper_id)
    return _paper_to_out(paper, is_parsed)


@router.get("/{paper_id:path}/chunks")
async def get_paper_chunks(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取论文的文本块"""
    paper_repo = PaperRepository(db)
    paper = paper_repo.get_visible(paper_id, user.id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    # 所有权校验：public 论文或拥有该 private 论文的用户才能查看 chunks
    user_paper_repo = UserPaperRepository(db, user.id)
    is_owner = (
        paper.visibility == "public"
        or paper.created_by_user_id == user.id
        or user_paper_repo.has_parsed(paper_id)
    )
    if not is_owner:
        raise HTTPException(status_code=403, detail="You do not have access to this paper's chunks")

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
