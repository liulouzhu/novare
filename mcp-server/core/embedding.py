"""向量化模块 - 百炼 text-embedding-v4 优先，本地 fallback"""

import logging
import os

import httpx

logger = logging.getLogger("research-server.embedding")

# 全局缓存
_embedder = None
_embedder_type = None


def _get_bailian_embedder():
    """百炼 text-embedding-v4（OpenAI 兼容 API）"""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    base_url = os.environ.get("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")

    if not api_key:
        return None

    logger.info("Using Bailian %s (base_url=%s)", model, base_url)
    return ("bailian", {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "dim": 1024,
    })


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
    """最简单的 hash-based 向量化（仅用于测试）"""
    import numpy as np

    class NumpyFallback:
        dim = 128

        def encode(self, text: str) -> list[float]:
            import hashlib
            h = hashlib.sha512(text.encode()).digest()
            data = h * (self.dim * 4 // len(h) + 1)
            arr = np.frombuffer(data[:self.dim * 4], dtype=np.uint32).astype(np.float32)
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm
            return arr.tolist()

        def encode_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.encode(t) for t in texts]

    logger.info("Using numpy hash fallback embedder (testing only)")
    return ("numpy_fallback", NumpyFallback())


def _init_embedder():
    """初始化嵌入模型，优先 百炼 → 本地 → numpy fallback"""
    global _embedder, _embedder_type

    result = _get_bailian_embedder()
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
    if embedder_type == "bailian":
        return embedder["dim"]
    elif embedder_type == "local":
        return 384
    else:
        return embedder.dim


# ── 异步版本（MCP 工具调用）─────────────────────────────────────────────

async def _bailian_embed_async(config: dict, texts: list[str]) -> list[list[float]]:
    """异步调用百炼 embedding API"""
    url = f"{config['base_url']}/embeddings"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    results = []
    batch_size = 25
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            body = {
                "model": config["model"],
                "input": batch,
                "dimensions": config["dim"],
            }
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            results.extend([item["embedding"] for item in sorted_data])
    return results


async def embed_text_async(text: str) -> list[float]:
    """异步单条向量化"""
    embedder_type, embedder = get_embedder()
    if embedder_type == "bailian":
        return (await _bailian_embed_async(embedder, [text]))[0]
    elif embedder_type == "local":
        return embedder.encode(text, normalize_embeddings=True).tolist()
    else:
        return embedder.encode(text)


async def embed_batch_async(texts: list[str]) -> list[list[float]]:
    """异步批量向量化"""
    if not texts:
        return []
    embedder_type, embedder = get_embedder()
    if embedder_type == "bailian":
        return await _bailian_embed_async(embedder, texts)
    elif embedder_type == "local":
        vecs = embedder.encode(texts, normalize_embeddings=True, batch_size=64)
        return [v.tolist() for v in vecs]
    else:
        return embedder.encode_batch(texts)


# ── 同步版本（非 MCP 场景）─────────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    """同步单条向量化"""
    embedder_type, embedder = get_embedder()
    if embedder_type == "bailian":
        url = f"{embedder['base_url']}/embeddings"
        headers = {
            "Authorization": f"Bearer {embedder['api_key']}",
            "Content-Type": "application/json",
        }
        body = {"model": embedder["model"], "input": text, "dimensions": embedder["dim"]}
        resp = httpx.post(url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    elif embedder_type == "local":
        return embedder.encode(text, normalize_embeddings=True).tolist()
    else:
        return embedder.encode(text)


def embed_batch(texts: list[str]) -> list[list[float]]:
    """同步批量向量化"""
    if not texts:
        return []
    embedder_type, embedder = get_embedder()
    if embedder_type == "bailian":
        url = f"{embedder['base_url']}/embeddings"
        headers = {"Authorization": f"Bearer {embedder['api_key']}", "Content-Type": "application/json"}
        results = []
        for i in range(0, len(texts), 25):
            batch = texts[i:i + 25]
            body = {"model": embedder["model"], "input": batch, "dimensions": embedder["dim"]}
            resp = httpx.post(url, json=body, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            results.extend([item["embedding"] for item in sorted_data])
        return results
    elif embedder_type == "local":
        vecs = embedder.encode(texts, normalize_embeddings=True, batch_size=64)
        return [v.tolist() for v in vecs]
    else:
        return embedder.encode_batch(texts)
