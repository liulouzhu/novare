"""RAG 语义检索工具 - 在已解析的论文库中进行语义检索

Redis 缓存：明确作用域的 RAG 查询在 TTL 内直接返回缓存结果，按 user_id 隔离。
"""

import logging
import os

import numpy as np

from core.database import get_connection, get_embeddings_by_paper_ids
from core.embedding import embed_text_async
from tools.result import ok, fail, truncate, MAX_CHUNK_TEXT

# 缓存 helper（纯计算，无 Redis 依赖）
from novare.cache import make_cache_key, cacheable_size

# Redis 访问（安全降级）
try:
    from web.backend.redis_service import redis_service
except Exception:
    redis_service = None

logger = logging.getLogger("research-server.rag_query")

_CACHE_TTL = 600  # 10 分钟

DEFAULT_USER_ID = os.getenv("RAG_DEFAULT_USER", "default")
ALLOW_UNSCOPED = os.getenv("RAG_ALLOW_UNSCOPED", "").lower() in ("1", "true", "yes")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算余弦相似度"""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


async def _get_user_paper_ids(user_id: str) -> set[str]:
    """Get paper_ids where user has fulltext access.

    Raises on any failure (fail-closed).  Returns an empty set when the
    user simply has no papers — callers treat that as "no results".
    """
    if not user_id:
        raise PermissionError("user_id is required for RAG search")

    from web.backend.db.base import get_session_factory
    from web.backend.db.models import UserPaper
    from uuid import UUID
    from sqlalchemy import select

    async with get_session_factory()() as db:
        result = await db.execute(
            select(UserPaper.paper_id).where(
                UserPaper.user_id == UUID(user_id),
                UserPaper.has_fulltext_access.is_(True),
            )
        )
        return {str(up[0]) for up in result.all()}


async def _brute_force_search(
    query_vec: np.ndarray, top_k: int, allowed_paper_ids: set[str]
) -> tuple[list[dict], int]:
    """Brute-force cosine similarity scoped to *allowed_paper_ids* only.

    Returns (results, total_scanned).  Never reads the full embeddings table.
    """
    if not allowed_paper_ids:
        return [], 0

    async with get_connection() as conn:
        scoped_embeddings = await get_embeddings_by_paper_ids(conn, allowed_paper_ids)

    if not scoped_embeddings:
        return [], 0

    results = []
    skipped_dim = 0
    for emb in scoped_embeddings:
        if len(emb["vec"]) != len(query_vec):
            skipped_dim += 1
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

    if skipped_dim > 0:
        logger.warning(
            "Brute-force search: skipped %d embeddings with mismatched dimension "
            "(query=%d). Papers may need re-parsing.", skipped_dim, len(query_vec)
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k], len(scoped_embeddings)


async def _milvus_search(query_vec: list[float], top_k: int, user_id: str) -> list[dict]:
    """Try Milvus vector search. Returns enriched results or raises on failure."""
    from core.vector_store import search_vectors

    hits = search_vectors(user_id, query_vec, top_k)
    if not hits:
        return []

    # Enrich with title/section from PostgreSQL
    results = []
    async with get_connection() as conn:
        from web.backend.db.models import Chunk, Paper
        from sqlalchemy import select
        for h in hits:
            result = await conn.execute(
                select(Paper.title, Chunk.section)
                .join(Chunk, Chunk.paper_id == Paper.id)
                .where(Chunk.id == h["chunk_id"])
            )
            row = result.first()
            title = row[0] if row else h["paper_id"]
            section = row[1] if row else ""
            results.append({
                "score": h["score"],
                "chunk_id": h["chunk_id"],
                "text": h["text"],
                "section": section,
                "paper_id": h["paper_id"],
                "title": title,
            })
    return results


# ── 缓存作用域判断 helpers ─────────────────────────────────────────────────


def _resolve_paper_ids(args: dict) -> list[str]:
    """从 args 中提取 paper_id/paper_ids，返回去重排序列表。"""
    ids: list[str] = []
    if "paper_id" in args and args["paper_id"]:
        ids.append(str(args["paper_id"]))
    if "paper_ids" in args and args["paper_ids"]:
        ids.extend(str(pid) for pid in args["paper_ids"] if pid)
    return sorted(set(ids))


def _has_explicit_filters(args: dict) -> bool:
    """判断 args 中是否有明确过滤字段（不含 question/top_k/paper_id/paper_ids）。"""
    filter_keys = {"filters", "paper_filter", "source", "year_from", "year_to"}
    return any(k in args and args[k] for k in filter_keys)


async def handle_rag_query(args: dict, user_id: str = None) -> str:
    """RAG 语义检索入口 — Milvus 优先，brute-force fallback。返回统一 JSON。

    安全策略：默认 fail-closed。
    - 无 user_id 且未显式启用 RAG_ALLOW_UNSCOPED → 拒绝。
    - 用户权限查询失败 → 拒绝（不降级为全库检索）。
    """
    question = args.get("question", "").strip()
    if not question:
        return fail("rag_query", "请提供查询问题。")

    top_k = args.get("top_k", 5)

    # ── 明确作用域检测 + Redis 缓存命中 ──
    # 只缓存有明确 paper_id/paper_ids/filter 的查询，不缓存全库搜索
    _scoped_paper_ids = _resolve_paper_ids(args)
    _has_filters = _has_explicit_filters(args)
    _is_scoped = bool(_scoped_paper_ids or _has_filters)

    _rs = redis_service
    cache_key: str | None = None
    if _is_scoped and user_id:
        cache_key = make_cache_key("rag_query", user_id, {
            "question": question,
            "top_k": top_k,
            "paper_ids": sorted(_scoped_paper_ids) if _scoped_paper_ids else [],
            "filters": {k: args[k] for k in sorted(args) if k not in ("question", "top_k", "paper_id", "paper_ids")},
        })
        if cache_key and _rs and _rs.is_available:
            try:
                cached = await _rs.get_json(cache_key)
                if cached and isinstance(cached, dict) and "result" in cached:
                    logger.info("rag_query cache hit: %s", cache_key)
                    return cached["result"]
            except Exception:
                logger.debug("cache read failed (non-fatal)", exc_info=True)

    # ── 权限校验：无 user_id 时默认拒绝 ──
    if not user_id and not ALLOW_UNSCOPED:
        return fail("rag_query", "缺少 user_id，无法执行安全的 RAG 检索。请在 Web 模式下使用。")

    # 计算查询向量
    try:
        query_vec_list = await embed_text_async(question)
        query_vec = np.array(query_vec_list, dtype=np.float32)
    except Exception as e:
        return fail("rag_query", f"查询向量化失败 - {e}")

    # ── 解析用户可访问的 paper_id 集合（fail-closed）──
    allowed_paper_ids: set[str] | None = None
    if user_id:
        try:
            allowed_paper_ids = await _get_user_paper_ids(user_id)
        except Exception as e:
            logger.error("RAG authorization failed for user %s: %s", user_id, e)
            return fail("rag_query", f"无法验证用户论文权限，检索已中止: {e}")
        if not allowed_paper_ids:
            return fail("rag_query", "您没有已解析全文的论文。请先使用 paper_parse 解析至少一篇论文。")

    top_results = []
    search_method = "brute-force"
    total_chunks = 0

    # 1. Try Milvus first (Milvus 自身按 user_id partition 隔离)
    try:
        milvus_user = user_id or DEFAULT_USER_ID
        top_results = await _milvus_search(query_vec_list, top_k, milvus_user)
        if top_results:
            search_method = "Milvus"
            total_chunks = len(top_results)
    except Exception as e:
        logger.warning("Milvus search failed, falling back to brute force: %s", e)

    # 2. Fallback: brute-force numpy search (scoped to allowed_paper_ids only)
    if not top_results:
        if not user_id and ALLOW_UNSCOPED:
            from core.database import get_all_embeddings
            async with get_connection() as conn:
                all_embeddings = await get_all_embeddings(conn)
            if not all_embeddings:
                return fail("rag_query", "论文库为空。请先使用 paper_parse 解析至少一篇论文。")
            results = []
            for emb in all_embeddings:
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
            top_results = results[:top_k]
            total_chunks = len(all_embeddings)
        else:
            top_results, total_chunks = await _brute_force_search(
                query_vec, top_k, allowed_paper_ids,
            )

    if not top_results:
        return fail("rag_query", "未找到相关内容。")

    # ── 构建结构化结果 ──
    results_json = []
    sources = []
    for i, r in enumerate(top_results, 1):
        results_json.append({
            "rank": i,
            "score": round(r["score"], 4),
            "chunk_id": r["chunk_id"],
            "paper_id": r["paper_id"],
            "title": r["title"],
            "section": r["section"],
            "text": truncate(r.get("text", ""), MAX_CHUNK_TEXT),
        })
        sources.append({
            "id": r["paper_id"],
            "title": r["title"],
            "section": r["section"],
            "chunk_id": r["chunk_id"],
        })

    unique_papers = len(set(r["paper_id"] for r in top_results))

    result = ok(
        "rag_query",
        {
            "question": question,
            "total_chunks_searched": total_chunks,
            "unique_papers": unique_papers,
            "search_method": search_method,
            "results": results_json,
        },
        summary=f"检索到 {len(top_results)} 条相关片段（来自 {unique_papers} 篇论文, {total_chunks} 个分块）",
        sources=sources,
        providers=[search_method],
    )

    # ── Redis 缓存：写入成功结果（仅限明确作用域查询） ──
    if cache_key and _rs and _rs.is_available:
        try:
            if cacheable_size(result):
                await _rs.set_json(cache_key, {"result": result}, ttl=_CACHE_TTL)
        except Exception:
            logger.debug("cache write failed (non-fatal)", exc_info=True)

    return result
