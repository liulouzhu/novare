"""PDF 解析工具 - 下载、解析、分块、向量化、写入数据库"""

import logging
import os
import tempfile
from typing import Optional

import httpx

from core.database import (
    get_connection,
    get_paper,
    get_chunks_by_paper,
    insert_chunks,
    insert_embeddings_batch,
    insert_citation,
    upsert_paper,
)
from core.embedding import embed_batch
from core.pdf_parser import (
    parse_pdf_to_markdown,
    split_into_sections,
    chunk_text,
    extract_references,
    extract_paper_ids_from_refs,
)

logger = logging.getLogger("research-server.paper_parse")

PAPERS_DIR = os.environ.get("RESEARCH_DATA_DIR", "./data")
PAPERS_DIR = os.path.join(PAPERS_DIR, "papers")


async def _download_pdf(url: str, dest_path: str) -> bool:
    """下载 PDF 文件"""
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(resp.content)
        logger.info("Downloaded PDF: %s -> %s", url, dest_path)
        return True
    except Exception as e:
        logger.error("Failed to download PDF from %s: %s", url, e)
        return False


async def handle_paper_parse(args: dict) -> str:
    """解析论文 PDF"""
    paper_id = args.get("paper_id")
    pdf_url = args.get("pdf_url")

    if not paper_id and not pdf_url:
        return "错误：请提供 paper_id 或 pdf_url。"

    os.makedirs(PAPERS_DIR, exist_ok=True)

    # 确定 PDF 来源
    pdf_path = None
    resolved_paper_id = paper_id

    with get_connection() as conn:
        # 尝试从数据库获取论文信息
        if paper_id:
            paper = get_paper(conn, paper_id)
            if paper:
                if paper.get("pdf_path") and os.path.exists(paper["pdf_path"]):
                    pdf_path = paper["pdf_path"]
                elif paper.get("source") == "arxiv":
                    arxiv_id = paper_id.replace("arxiv:", "")
                    pdf_url = pdf_url or f"https://arxiv.org/pdf/{arxiv_id}"
                elif paper.get("source") == "semantic_scholar":
                    # 尝试从 Semantic Scholar 获取 PDF
                    if not pdf_url:
                        pdf_url = _try_get_s2_pdf_url(paper_id)

    # 下载 PDF
    if not pdf_path:
        if pdf_url:
            # 生成文件名
            safe_id = (resolved_paper_id or "unknown").replace("/", "_").replace(":", "_")
            pdf_path = os.path.join(PAPERS_DIR, f"{safe_id}.pdf")
            if not os.path.exists(pdf_path):
                success = await _download_pdf(pdf_url, pdf_path)
                if not success:
                    return f"错误：无法从 {pdf_url} 下载 PDF。"
        else:
            return "错误：无法确定 PDF 来源。请提供 pdf_url。"

    if not os.path.exists(pdf_path):
        return f"错误：PDF 文件不存在: {pdf_path}"

    # 检查是否已经解析过
    with get_connection() as conn:
        if resolved_paper_id:
            existing_chunks = get_chunks_by_paper(conn, resolved_paper_id)
            if existing_chunks:
                return (
                    f"论文 {resolved_paper_id} 已解析（{len(existing_chunks)} 个分块）。"
                    f"如需重新解析，请先删除相关数据。"
                )

    # 解析 PDF
    try:
        markdown_text = parse_pdf_to_markdown(pdf_path)
    except Exception as e:
        return f"错误：PDF 解析失败 - {str(e)}"

    if not markdown_text or len(markdown_text) < 100:
        return "错误：PDF 解析结果为空或过短。"

    # 按章节分割
    sections = split_into_sections(markdown_text)

    # 分块
    all_chunks = []
    for sec in sections:
        sec_chunks = chunk_text(sec["text"])
        for i, chunk_text_str in enumerate(sec_chunks):
            all_chunks.append({
                "section": sec["section"],
                "ordinal": i,
                "text": chunk_text_str,
            })

    if not all_chunks:
        return "错误：分块结果为空。"

    # 向量化
    try:
        texts = [c["text"] for c in all_chunks]
        embeddings = embed_batch(texts)
    except Exception as e:
        logger.warning("Embedding failed, saving chunks without vectors: %s", e)
        embeddings = None

    # 写入数据库
    with get_connection() as conn:
        # 确保论文存在
        if resolved_paper_id:
            existing = get_paper(conn, resolved_paper_id)
            if not existing:
                # 从 PDF 内容提取基本信息
                title = _extract_title_from_markdown(markdown_text)
                upsert_paper(conn, {
                    "id": resolved_paper_id,
                    "title": title or resolved_paper_id,
                    "authors": [],
                    "abstract": "",
                    "year": None,
                    "source": "parsed",
                    "pdf_path": pdf_path,
                    "url": pdf_url,
                    "citation_count": 0,
                })
            else:
                # 更新 pdf_path
                conn.execute(
                    "UPDATE papers SET pdf_path=? WHERE id=?",
                    (pdf_path, resolved_paper_id),
                )

        # 插入分块
        chunk_ids = insert_chunks(conn, resolved_paper_id, all_chunks)

        # 插入向量
        if embeddings:
            insert_embeddings_batch(conn, chunk_ids, embeddings)

        # 提取参考文献并建立引用关系
        refs = extract_references(markdown_text)
        ref_ids = extract_paper_ids_from_refs(refs)
        citations_added = 0
        for ref in ref_ids:
            if ref.get("doi"):
                target_id = f"doi:{ref['doi']}"
            elif ref.get("arxiv_id"):
                target_id = f"arxiv:{ref['arxiv_id']}"
            else:
                continue
            insert_citation(conn, resolved_paper_id, target_id)
            citations_added += 1

    # 构建返回结果
    section_summary = []
    for sec in sections:
        preview = sec["text"][:200].replace("\n", " ")
        section_summary.append(f"  [{sec['section']}] {preview}...")

    result_parts = [
        f"✅ 论文解析完成: {resolved_paper_id}",
        f"   PDF: {pdf_path}",
        f"   章节数: {len(sections)}",
        f"   分块数: {len(all_chunks)}",
        f"   向量化: {'✅ 完成' if embeddings else '❌ 跳过'}",
        f"   引用关系: {citations_added} 条",
        "",
        "## 章节结构",
        *section_summary,
    ]

    if refs:
        result_parts.append("")
        result_parts.append(f"## 参考文献（前 10 条，共 {len(refs)} 条）")
        for ref in refs[:10]:
            result_parts.append(f"  - {ref[:150]}")

    return "\n".join(result_parts)


def _try_get_s2_pdf_url(paper_id: str) -> Optional[str]:
    """尝试从 Semantic Scholar 获取 PDF URL"""
    s2_id = paper_id.replace("doi:", "").replace("s2:", "")
    try:
        import httpx
        resp = httpx.get(
            f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}",
            params={"fields": "openAccessPdf"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            oa = data.get("openAccessPdf")
            if oa:
                return oa.get("url")
    except Exception:
        pass
    return None


def _extract_title_from_markdown(md: str) -> str:
    """从 Markdown 开头提取标题"""
    for line in md.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line and len(line) > 10:
            return line[:200]
    return ""
