"""Qwen3 reranker client used after RRF candidate fusion.

Environment variables:
    RAG_RERANK_API_KEY      Optional; falls back to DASHSCOPE_API_KEY.
    RAG_RERANK_URL          DashScope text-rerank endpoint.
    RAG_RERANK_MODEL        Model name, default qwen3-rerank.
    RAG_RERANK_TIMEOUT      Request timeout in seconds.
    RAG_RERANK_MAX_DOC_CHARS Maximum characters sent for each candidate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_RERANK_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/"
    "rerank/text-rerank/text-rerank"
)


@dataclass(frozen=True)
class RerankResult:
    """Result that keeps provider failures separate from valid rankings."""

    hits: list[dict]
    available: bool
    error: str | None = None


def _candidate_document(candidate: dict, max_chars: int) -> str:
    parts = []
    title = str(candidate.get("title") or "").strip()
    section = str(candidate.get("section") or "").strip()
    text = str(candidate.get("text") or "").strip()
    if title:
        parts.append(f"Title: {title}")
    if section:
        parts.append(f"Section: {section}")
    if text:
        parts.append(text)
    return "\n".join(parts)[:max_chars]


async def rerank_chunks(query: str, candidates: list[dict]) -> RerankResult:
    """Rerank RRF candidates with DashScope's qwen3-rerank model.

    The returned dictionaries are copies of the input candidates with
    ``rerank_score`` and ``rerank_rank`` added. Provider/configuration errors
    are returned as ``available=False`` so callers can safely retain RRF order.
    """
    if not candidates:
        return RerankResult([], True)

    api_key = os.getenv("RAG_RERANK_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return RerankResult(
            [], False,
            "RAG_RERANK_API_KEY and DASHSCOPE_API_KEY are both unset",
        )

    url = os.getenv("RAG_RERANK_URL", DEFAULT_RERANK_URL).strip()
    model = os.getenv("RAG_RERANK_MODEL", "qwen3-rerank").strip()
    try:
        timeout = max(1.0, float(os.getenv("RAG_RERANK_TIMEOUT", "30")))
        max_chars = max(256, int(os.getenv("RAG_RERANK_MAX_DOC_CHARS", "6000")))
    except ValueError as exc:
        return RerankResult([], False, f"invalid rerank configuration: {exc}")

    documents = [_candidate_document(item, max_chars) for item in candidates]
    body = {
        "model": model,
        "input": {"query": query, "documents": documents},
        "parameters": {
            "return_documents": False,
            "top_n": len(documents),
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return RerankResult([], False, str(exc))

    output = payload.get("output", payload)
    raw_results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(raw_results, list):
        return RerankResult([], False, "rerank response is missing output.results")

    ranked: list[dict] = []
    seen: set[int] = set()
    try:
        for item in raw_results:
            index = int(item["index"])
            if index < 0 or index >= len(candidates) or index in seen:
                continue
            hit = dict(candidates[index])
            hit["rerank_score"] = float(item["relevance_score"])
            hit["rerank_rank"] = len(ranked) + 1
            ranked.append(hit)
            seen.add(index)
    except (KeyError, TypeError, ValueError) as exc:
        return RerankResult([], False, f"invalid rerank result item: {exc}")

    if len(ranked) != len(candidates):
        return RerankResult(
            [], False,
            f"rerank returned {len(ranked)} valid results for {len(candidates)} candidates",
        )
    return RerankResult(ranked, True)
