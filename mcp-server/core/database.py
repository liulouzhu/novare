"""PostgreSQL 数据库管理 - 论文元数据、分块、向量、引用关系

所有函数通过 get_connection() 获取 SQLAlchemy Session，函数签名保持与原 SQLite 版本兼容。
conn 参数类型为 sqlalchemy.orm.Session（支持 with 语句）。
"""

import json
import logging
from contextlib import contextmanager
from typing import Optional

import numpy as np

logger = logging.getLogger("research-server.db")


def _get_session_factory():
    """延迟导入 SessionLocal，避免循环依赖。"""
    from web.backend.db.base import SessionLocal
    return SessionLocal


@contextmanager
def get_connection():
    """获取 PostgreSQL session 的上下文管理器。

    用法与原 SQLite 版本一致:
        with get_connection() as conn:
            paper = get_paper(conn, paper_id)
    """
    SessionLocal = _get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Paper CRUD ────────────────────────────────────────────────────────────

def upsert_paper(conn, paper: dict) -> None:
    """插入或更新论文元数据"""
    from web.backend.db.models import Paper

    # Normalize authors: accept both JSON string and list
    authors = paper.get("authors", [])
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except (json.JSONDecodeError, TypeError):
            authors = [authors] if authors else []

    existing = conn.query(Paper).filter(Paper.id == paper["id"]).first()
    if existing:
        existing.title = paper["title"]
        existing.authors = authors
        if paper.get("abstract"):
            existing.abstract = paper["abstract"]
        if paper.get("year"):
            existing.year = paper["year"]
        if paper.get("source"):
            existing.source = paper["source"]
        if paper.get("pdf_path"):
            existing.pdf_path = paper["pdf_path"]
        if paper.get("url"):
            existing.url = paper["url"]
        if paper.get("citation_count"):
            existing.citation_count = max(
                existing.citation_count or 0, paper["citation_count"]
            )
    else:
        conn.add(Paper(
            id=paper["id"],
            title=paper["title"],
            authors=authors,
            abstract=paper.get("abstract"),
            year=paper.get("year"),
            source=paper.get("source"),
            pdf_path=paper.get("pdf_path"),
            url=paper.get("url"),
            citation_count=paper.get("citation_count", 0),
        ))
    conn.flush()


def get_paper(conn, paper_id: str) -> Optional[dict]:
    """获取单篇论文，返回 dict 或 None"""
    from web.backend.db.models import Paper

    row = conn.query(Paper).filter(Paper.id == paper_id).first()
    if not row:
        return None
    return {
        "id": row.id,
        "title": row.title,
        "authors": row.authors if isinstance(row.authors, list) else json.loads(row.authors or "[]"),
        "abstract": row.abstract,
        "year": row.year,
        "source": row.source,
        "pdf_path": row.pdf_path,
        "url": row.url,
        "citation_count": row.citation_count,
        "created_at": str(row.created_at) if row.created_at else None,
    }


def get_all_papers(conn) -> list[dict]:
    """获取所有论文"""
    from web.backend.db.models import Paper

    rows = conn.query(Paper).order_by(Paper.created_at.desc()).all()
    return [{
        "id": r.id, "title": r.title, "authors": r.authors,
        "abstract": r.abstract, "year": r.year, "source": r.source,
        "pdf_path": r.pdf_path, "url": r.url, "citation_count": r.citation_count,
    } for r in rows]


# ── Chunk CRUD ────────────────────────────────────────────────────────────

def insert_chunks(conn, paper_id: str, chunks: list[dict]) -> list[int]:
    """批量插入分块，返回 chunk_id 列表"""
    from web.backend.db.models import Chunk

    ids = []
    for chunk in chunks:
        obj = Chunk(
            paper_id=paper_id,
            section=chunk.get("section"),
            ordinal=chunk.get("ordinal", 0),
            text=chunk["text"],
        )
        conn.add(obj)
        conn.flush()  # 获取自增 id
        ids.append(obj.id)
    return ids


def get_chunks_by_paper(conn, paper_id: str) -> list[dict]:
    """获取论文的所有分块"""
    from web.backend.db.models import Chunk

    rows = (
        conn.query(Chunk)
        .filter(Chunk.paper_id == paper_id)
        .order_by(Chunk.ordinal)
        .all()
    )
    return [{"id": r.id, "paper_id": r.paper_id, "section": r.section,
             "ordinal": r.ordinal, "text": r.text} for r in rows]


def get_all_chunks(conn) -> list[dict]:
    """获取所有分块"""
    from web.backend.db.models import Chunk

    rows = conn.query(Chunk).order_by(Chunk.paper_id, Chunk.ordinal).all()
    return [{"id": r.id, "paper_id": r.paper_id, "section": r.section,
             "ordinal": r.ordinal, "text": r.text} for r in rows]


# ── Embedding CRUD ────────────────────────────────────────────────────────

def insert_embeddings_batch(conn, chunk_ids: list[int], vecs: list[list[float]]) -> None:
    """批量插入向量"""
    from web.backend.db.models import Embedding

    for chunk_id, vec in zip(chunk_ids, vecs):
        arr = np.array(vec, dtype=np.float32)
        conn.add(Embedding(
            chunk_id=chunk_id,
            dim=len(arr),
            vec=arr.tobytes(),
        ))
    conn.flush()


def get_all_embeddings(conn) -> list[dict]:
    """获取所有向量（用于 brute-force cosine similarity 检索）"""
    from web.backend.db.models import Embedding, Chunk, Paper
    from sqlalchemy import select

    rows = (
        conn.query(Embedding, Chunk, Paper)
        .join(Chunk, Embedding.chunk_id == Chunk.id)
        .join(Paper, Chunk.paper_id == Paper.id)
        .all()
    )
    results = []
    for emb, chunk, paper in rows:
        vec = np.frombuffer(emb.vec, dtype=np.float32).copy()
        results.append({
            "chunk_id": emb.chunk_id,
            "dim": emb.dim,
            "vec": vec,
            "text": chunk.text,
            "section": chunk.section,
            "paper_id": chunk.paper_id,
            "title": paper.title,
        })
    return results


# ── Citation CRUD ─────────────────────────────────────────────────────────

def insert_citation(conn, source_id: str, target_id: str) -> None:
    """插入引用关系（忽略重复）"""
    from web.backend.db.models import Citation

    existing = conn.query(Citation).filter(
        Citation.source_id == source_id,
        Citation.target_id == target_id,
    ).first()
    if not existing:
        conn.add(Citation(source_id=source_id, target_id=target_id))
        conn.flush()


def get_citations(conn, paper_id: str) -> dict:
    """获取论文的引用关系"""
    from web.backend.db.models import Citation

    citing = (
        conn.query(Citation.target_id)
        .filter(Citation.source_id == paper_id)
        .all()
    )
    cited_by = (
        conn.query(Citation.source_id)
        .filter(Citation.target_id == paper_id)
        .all()
    )
    return {
        "citing": [r[0] for r in citing],
        "cited_by": [r[0] for r in cited_by],
    }
