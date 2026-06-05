"""论文检索工具 - 并行查询 Semantic Scholar + arXiv"""

import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from core.database import get_connection, upsert_paper

logger = logging.getLogger("research-server.paper_search")

# Semantic Scholar API
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,authors,abstract,year,externalIds,citationCount,openAccessPdf"

# arXiv API
ARXIV_API = "https://export.arxiv.org/api/query"


async def _search_semantic_scholar(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    limit: int = 10,
) -> tuple[list[dict], str]:
    """查询 Semantic Scholar API。返回 (结果列表, 错误信息)"""
    params = {
        "query": query,
        "limit": min(limit, 20),
        "fields": S2_FIELDS,
    }
    if year_from or year_to:
        year_range = f"{year_from or ''}-{year_to or ''}"
        params["year"] = year_range

    headers = {"User-Agent": "Novare-ResearchAgent/0.1"}
    s2_key = os.environ.get("S2_API_KEY")
    if s2_key:
        params["api-key"] = s2_key

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30, headers=headers) as client:
                resp = await client.get(S2_API, params=params)

                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("Semantic Scholar rate limited, retrying in %ds (attempt %d/3)", wait, attempt + 1)
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

            results = []
            for paper in data.get("data", []):
                ext_ids = paper.get("externalIds", {}) or {}
                paper_id = (
                    ext_ids.get("DOI")
                    or ext_ids.get("ArXiv")
                    or paper.get("paperId", "")
                )
                if not paper_id:
                    continue

                # 确定 ID 类型
                if ext_ids.get("DOI"):
                    source_id = f"doi:{ext_ids['DOI']}"
                elif ext_ids.get("ArXiv"):
                    source_id = f"arxiv:{ext_ids['ArXiv']}"
                else:
                    source_id = f"s2:{paper.get('paperId', '')}"

                authors = [a.get("name", "") for a in (paper.get("authors") or [])]
                pdf_url = None
                oa = paper.get("openAccessPdf")
                if oa:
                    pdf_url = oa.get("url")

                results.append({
                    "id": source_id,
                    "title": paper.get("title", ""),
                    "authors": authors,
                    "abstract": paper.get("abstract", ""),
                    "year": paper.get("year"),
                    "source": "semantic_scholar",
                    "url": f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}",
                    "pdf_url": pdf_url,
                    "citation_count": paper.get("citationCount", 0),
                })
            return results, ""

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                if attempt < 2:
                    continue
                return [], "Semantic Scholar: 请求频率超限(429)，请稍后重试"
            logger.error("Semantic Scholar API error: %s", e)
            return [], f"Semantic Scholar: HTTP {e.response.status_code}"
        except Exception as e:
            logger.error("Semantic Scholar search failed: %s", e)
            return [], f"Semantic Scholar: {type(e).__name__}"

    return [], "Semantic Scholar: 请求频率超限(429)，已重试3次"


async def _search_arxiv(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    limit: int = 10,
) -> tuple[list[dict], str]:
    """查询 arXiv API。返回 (结果列表, 错误信息)"""
    # 构建搜索查询
    search_query = f"all:{query}"
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": min(limit, 20),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }

    try:
        async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "Novare-ResearchAgent/0.1"}) as client:
            resp = await client.get(ARXIV_API, params=params)
            resp.raise_for_status()
            xml_text = resp.text

        # 解析 Atom XML
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        root = ET.fromstring(xml_text)

        results = []
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", "", ns).strip().replace("\n", " ")
            summary = entry.findtext("atom:summary", "", ns).strip().replace("\n", " ")

            # 提取 arXiv ID
            entry_id = entry.findtext("atom:id", "", ns)
            arxiv_id = entry_id.split("/abs/")[-1] if "/abs/" in entry_id else entry_id
            # 去掉版本号
            arxiv_base = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id

            # 作者
            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.findtext("atom:name", "", ns)
                if name:
                    authors.append(name)

            # 发布年份
            published = entry.findtext("atom:published", "")
            year = int(published[:4]) if published and len(published) >= 4 else None

            # 年份过滤
            if year_from and year and year < year_from:
                continue
            if year_to and year and year > year_to:
                continue

            # PDF 链接
            pdf_url = None
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href")
                    break

            results.append({
                "id": f"arxiv:{arxiv_base}",
                "title": title,
                "authors": authors,
                "abstract": summary[:500],  # arXiv 摘要可能很长
                "year": year,
                "source": "arxiv",
                "url": f"https://arxiv.org/abs/{arxiv_base}",
                "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_base}",
                "citation_count": 0,  # arXiv API 不提供引用数
            })
        return results, ""

    except Exception as e:
        logger.error("arXiv search failed: %s", e)
        return [], f"arXiv: {type(e).__name__}"


def _merge_results(s2_results: list[dict], arxiv_results: list[dict], limit: int) -> list[dict]:
    """合并去重两个源的结果"""
    seen_ids = set()
    merged = []

    # 优先 Semantic Scholar（有引用数）
    for paper in s2_results:
        key = paper["id"]
        if key not in seen_ids:
            seen_ids.add(key)
            merged.append(paper)

    # 补充 arXiv 结果
    for paper in arxiv_results:
        key = paper["id"]
        if key not in seen_ids:
            seen_ids.add(key)
            merged.append(paper)

    # 按引用数排序
    merged.sort(key=lambda p: p.get("citation_count", 0), reverse=True)
    return merged[:limit]


def _format_results(papers: list[dict]) -> str:
    """格式化论文列表为可读文本"""
    if not papers:
        return "未找到相关论文。请尝试不同的搜索词。"

    lines = [f"找到 {len(papers)} 篇相关论文：\n"]
    for i, p in enumerate(papers, 1):
        authors_str = ", ".join(p["authors"][:3])
        if len(p["authors"]) > 3:
            authors_str += " et al."

        lines.append(f"**{i}. {p['title']}**")
        lines.append(f"   作者: {authors_str}")
        lines.append(f"   年份: {p.get('year', 'N/A')} | 引用: {p.get('citation_count', 0)} | 来源: {p.get('source', 'N/A')}")
        lines.append(f"   ID: {p['id']}")

        if p.get("pdf_url"):
            lines.append(f"   PDF: {p['pdf_url']}")

        abstract = p.get("abstract", "")
        if abstract:
            lines.append(f"   摘要: {abstract[:300]}{'...' if len(abstract) > 300 else ''}")
        lines.append("")

    return "\n".join(lines)


async def handle_paper_search(args: dict) -> str:
    """论文检索入口"""
    query = args.get("query", "").strip()
    if not query:
        return "错误：请提供搜索关键词。"

    year_from = args.get("year_from")
    year_to = args.get("year_to")
    limit = min(args.get("limit", 10), 20)

    # 并行查询两个源
    s2_task = _search_semantic_scholar(query, year_from, year_to, limit)
    arxiv_task = _search_arxiv(query, year_from, year_to, limit)
    (s2_results, s2_err), (arxiv_results, arxiv_err) = await asyncio.gather(s2_task, arxiv_task)

    # 合并结果
    merged = _merge_results(s2_results, arxiv_results, limit)

    # 写入数据库
    if merged:
        try:
            with get_connection() as conn:
                for paper in merged:
                    upsert_paper(conn, paper)
        except Exception as e:
            logger.warning("Failed to save papers to DB: %s", e)

    # 有结果就返回结果
    if merged:
        return _format_results(merged)

    # 两个源都失败了，报告具体错误
    errors = [e for e in (s2_err, arxiv_err) if e]
    if errors:
        return f"搜索失败，所有数据源均不可用：\n" + "\n".join(f"- {e}" for e in errors) + "\n请稍后重试。"

    return "未找到相关论文。请尝试不同的搜索词。"
