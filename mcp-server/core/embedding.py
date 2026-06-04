"""向量化模块 - OpenAI API 优先，sentence-transformers 本地 fallback"""

import logging
import os
from typing import Optional

logger = logging.getLogger("research-server.embedding")

# 全局缓存，避免重复初始化
_embedder = None
_embedder_type = None


def _get_openai_embedder():
    """OpenAI text-embedding-3-small"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        logger.info("Using OpenAI text-embedding-3-small")
        return ("openai", client)
    except ImportError:
        logger.warning("openai package not installed, skipping OpenAI embedder")
        return None


def _get_local_embedder():
    """sentence-transformers all-MiniLM-L6-v2 本地模型"""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Using local sentence-transformers all-MiniLM-L6-v2")
        return ("local", model)
    except ImportError:
        logger.warning("sentence-transformers not installed, using numpy fallback")
        return None


def _get_numpy_fallback():
    """最简单的 hash-based 向量化（仅用于测试，不推荐生产使用）"""
    import numpy as np

    class NumpyFallback:
        """基于字符 hash 的伪向量化，维度 128，仅供测试"""
        dim = 128

        def encode(self, text: str) -> list[float]:
            import hashlib
            h = hashlib.sha512(text.encode()).digest()
            # 重复 hash 以填充维度
            data = h * (self.dim * 4 // len(h) + 1)
            arr = np.frombuffer(data[:self.dim * 4], dtype=np.uint32).astype(np.float32)
            # 归一化
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm
            return arr.tolist()

        def encode_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.encode(t) for t in texts]

    logger.info("Using numpy hash fallback embedder (testing only)")
    return ("numpy_fallback", NumpyFallback())


def _init_embedder():
    """初始化嵌入模型，优先 OpenAI → 本地 → numpy fallback"""
    global _embedder, _embedder_type

    result = _get_openai_embedder()
    if result:
        _embedder_type, _embedder = result
        return

    result = _get_local_embedder()
    if result:
        _embedder_type, _embedder = result
        return

    _embedder_type, _embedder = _get_numpy_fallback()


def get_embedder():
    """获取嵌入模型实例"""
    global _embedder
    if _embedder is None:
        _init_embedder()
    return _embedder_type, _embedder


def get_embedding_dim() -> int:
    """获取向量维度"""
    embedder_type, embedder = get_embedder()
    if embedder_type == "openai":
        return 1536
    elif embedder_type == "local":
        return 384
    else:
        return embedder.dim


def embed_text(text: str) -> list[float]:
    """对单个文本进行向量化"""
    embedder_type, embedder = get_embedder()

    if embedder_type == "openai":
        resp = embedder.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return resp.data[0].embedding

    elif embedder_type == "local":
        vec = embedder.encode(text, normalize_embeddings=True)
        return vec.tolist()

    else:
        return embedder.encode(text)


def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量向量化"""
    if not texts:
        return []

    embedder_type, embedder = get_embedder()

    if embedder_type == "openai":
        # OpenAI 支持批量，但有 token 限制，分批处理
        results = []
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = embedder.embeddings.create(
                model="text-embedding-3-small",
                input=batch,
            )
            for item in resp.data:
                results.append(item.embedding)
        return results

    elif embedder_type == "local":
        vecs = embedder.encode(texts, normalize_embeddings=True, batch_size=64)
        return [v.tolist() for v in vecs]

    else:
        return embedder.encode_batch(texts)
