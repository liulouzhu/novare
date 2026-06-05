"""RAG 语义检索工具 - 在已解析的论文库中进行语义检索"""

import logging

import numpy as np

from core.database import get_connection, get_all_embeddings
from core.embedding import embed_text_async

logger = logging.getLogger("research-server.rag_query")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算余弦相似度"""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


async def handle_rag_query(args: dict) -> str:
    """RAG 语义检索入口"""
    question = args.get("question", "").strip()
    if not question:
        return "错误：请提供查询问题。"

    top_k = args.get("top_k", 5)

    # 计算查询向量
    try:
        query_vec = np.array(await embed_text_async(question), dtype=np.float32)
    except Exception as e:
        return f"错误：查询向量化失败 - {str(e)}"

    # 加载所有 chunk embeddings
    with get_connection() as conn:
        all_embeddings = get_all_embeddings(conn)

    if not all_embeddings:
        return "论文库为空。请先使用 paper_parse 解析至少一篇论文。"

    # 计算 cosine similarity
    results = []
    for emb in all_embeddings:
        # 确保维度匹配
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

    # 按相似度排序
    results.sort(key=lambda x: x["score"], reverse=True)
    top_results = results[:top_k]

    if not top_results:
        return "未找到相关内容。请确保已解析的论文与查询主题相关。"

    # 格式化输出
    lines = [f"语义检索结果（Top {len(top_results)}）：\n"]
    for i, r in enumerate(top_results, 1):
        snippet = r["text"][:480].replace("\n", " ")
        lines.append(f"**#{i} | 相似度: {r['score']:.3f} | 论文: {r['title']}**")
        lines.append(f"   章节: {r['section']} | 论文ID: {r['paper_id']}")
        lines.append(f"   片段: {snippet}{'...' if len(r['text']) > 480 else ''}")
        lines.append("")

    # 统计信息
    unique_papers = len(set(r["paper_id"] for r in top_results))
    lines.append(f"---")
    lines.append(f"检索自 {len(all_embeddings)} 个文本分块，涉及 {unique_papers} 篇论文。")

    return "\n".join(lines)
