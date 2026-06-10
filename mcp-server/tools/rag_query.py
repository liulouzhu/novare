"""RAG 语义检索工具 - 在已解析的论文库中进行语义检索"""

import logging
import os

import numpy as np

from core.database import get_connection, get_all_embeddings
from core.embedding import embed_text_async

logger = logging.getLogger("research-server.rag_query")

DEFAULT_USER_ID = os.getenv("RAG_DEFAULT_USER", "default")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算余弦相似度"""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _get_user_paper_ids(user_id: str) -> set[str] | None:
    """Get paper_ids where user has fulltext access (parsed/uploaded).
    Returns None if user_id is not provided or lookup fails."""
    if not user_id:
        return None
    try:
        from web.backend.db.base import SessionLocal
        from web.backend.db.models import UserPaper
        from uuid import UUID
        db = SessionLocal()
        try:
            return {
                str(up.paper_id)
                for up in db.query(UserPaper.paper_id)
                .filter(
                    UserPaper.user_id == UUID(user_id),
                    UserPaper.has_fulltext_access.is_(True),
                )
                .all()
            }
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to get user papers for filtering: %s", e)
        return None


def _brute_force_search(
    query_vec: np.ndarray, top_k: int, user_id: str = None
) -> list[dict]:
    """Fallback: brute-force numpy cosine similarity over all SQLite embeddings."""
    allowed_paper_ids = _get_user_paper_ids(user_id)
    if user_id and allowed_paper_ids is not None and not allowed_paper_ids:
        return []  # User has no papers — empty result

    with get_connection() as conn:
        all_embeddings = get_all_embeddings(conn)

    if not all_embeddings:
        return []

    results = []
    for emb in all_embeddings:
        if allowed_paper_ids is not None and emb["paper_id"] not in allowed_paper_ids:
            continue
        if len(emb["vec"]) != len(query_vec):
            continue
        score = _cosine_similarity(query_vec, emb["vec"])
        results.append({
            "score": score,
            "chunk_id": emb["chunk_id"],
            "text": emb["text"],
            "section": emb["section"],
            "paper_id": emb["paper_id"],
            "title": emb["title"],
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def _milvus_search(query_vec: list[float], top_k: int, user_id: str) -> list[dict]:
    """Try Milvus vector search. Returns enriched results or raises on failure."""
    from core.vector_store import search_vectors

    hits = search_vectors(user_id, query_vec, top_k)
    if not hits:
        return []

    # Enrich with title/section from SQLite
    results = []
    with get_connection() as conn:
        for h in hits:
            row = conn.execute(
                "SELECT p.title, c.section FROM chunks c "
                "JOIN papers p ON c.paper_id = p.id WHERE c.id = ?",
                (h["chunk_id"],),
            ).fetchone()
            title = row["title"] if row else h["paper_id"]
            section = row["section"] if row else ""
            results.append({
                "score": h["score"],
                "chunk_id": h["chunk_id"],
                "text": h["text"],
                "section": section,
                "paper_id": h["paper_id"],
                "title": title,
            })
    return results


async def handle_rag_query(args: dict, user_id: str = None) -> str:
    """RAG 语义检索入口 — Milvus 优先，brute-force fallback"""
    question = args.get("question", "").strip()
    if not question:
        return "错误：请提供查询问题。"

    top_k = args.get("top_k", 5)

    # 计算查询向量
    try:
        query_vec_list = await embed_text_async(question)
        query_vec = np.array(query_vec_list, dtype=np.float32)
    except Exception as e:
        return f"错误：查询向量化失败 - {str(e)}"

    top_results = []
    search_method = "brute-force"

    # 1. Try Milvus first
    try:
        top_results = _milvus_search(query_vec_list, top_k, user_id or DEFAULT_USER_ID)
        if top_results:
            search_method = "Milvus"
    except Exception as e:
        logger.warning("Milvus search failed, falling back to brute force: %s", e)

    # 2. Fallback: brute-force numpy search
    if not top_results:
        top_results = _brute_force_search(query_vec, top_k, user_id=user_id)

    if not top_results:
        return "论文库为空。请先使用 paper_parse 解析至少一篇论文。"

    lines = [f"语义检索结果（Top {len(top_results)}）：\n"]
    for i, r in enumerate(top_results, 1):
        snippet = r["text"][:480].replace("\n", " ")
        lines.append(f"**#{i} | 相似度: {r['score']:.3f} | 论文: {r['title']}**")
        lines.append(f"   章节: {r['section']} | 论文ID: {r['paper_id']}")
        lines.append(f"   片段: {snippet}{'...' if len(r['text']) > 480 else ''}")
        lines.append("")

    unique_papers = len(set(r["paper_id"] for r in top_results))

    # Count total chunks for the summary line
    if search_method == "Milvus":
        total_chunks = len(top_results)  # Milvus doesn't expose total easily
    else:
        with get_connection() as conn:
            all_embeddings = get_all_embeddings(conn)
        total_chunks = len(all_embeddings)

    lines.append("---")
    lines.append(
        f"检索自 {total_chunks} 个文本分块，涉及 {unique_papers} 篇论文。"
        f"（检索方式: {search_method}）"
    )

    return "\n".join(lines)
