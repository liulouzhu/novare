"""PDF 解析工具 - 下载、解析、分块、向量化、写入数据库"""

import logging
import os
import tempfile
from pathlib import Path
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
from core.embedding import embed_batch_async, get_embedding_dim, EmbeddingProviderError
from core.paper_id import canonicalize_paper_id
from core.pdf_parser import (
    split_into_sections,
    chunk_text,
    extract_references,
    extract_paper_ids_from_refs,
)
from core.mineru import parse_pdf_with_mineru
from tools.result import ok, fail, truncate, MAX_SECTION_PREVIEW, MAX_SECTIONS, MAX_REFS, MAX_REF_LEN

logger = logging.getLogger("research-server.paper_parse")

_GLOBAL_PAPERS_DIR = os.path.join(
    os.environ.get("RESEARCH_DATA_DIR", "./data"), "public_papers",
)
DEFAULT_USER_ID = os.getenv("RAG_DEFAULT_USER", "default")


def _public_papers_dir() -> str:
    """全局公共 PDF 缓存（arxiv / S2 下载的论文）。"""
    return _GLOBAL_PAPERS_DIR


def _user_papers_dir(user_id: str) -> str:
    """用户私有 PDF 目录（用户上传的论文）。"""
    from novare.config import get_user_workspace
    return os.path.join(get_user_workspace(user_id), "papers")


def _resolve_user_workspace(user_id: str) -> Path:
    """返回用户 workspace 的 resolved Path。"""
    from novare.config import get_user_workspace
    return Path(get_user_workspace(user_id)).resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    """判断 path 是否在 root 之下（兼容 Python <3.9）。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_user_local_file(file_path: str, user_id: str | None) -> str:
    """校验本地文件路径属于当前用户的允许目录。

    返回 resolved 路径字符串。
    Raises:
        PermissionError — 无权限或缺少 user context
        FileNotFoundError — 文件不存在
    """
    resolved = Path(file_path).resolve()

    if not user_id:
        if os.getenv("ALLOW_UNSCOPED_LOCAL_FILE_PARSE", "").lower() not in ("1", "true", "yes"):
            raise PermissionError("缺少用户上下文，无法解析本地文件。请在 Web 模式下使用。")
        if not resolved.is_file():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return str(resolved)

    user_root = _resolve_user_workspace(user_id)
    allowed_roots = [
        (user_root / "uploads").resolve(),
        (user_root / "papers").resolve(),
    ]

    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        raise PermissionError("文件路径不在您的允许目录内，无法访问。")

    if not resolved.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    return str(resolved)


async def _user_has_fulltext_access(user_id: str, paper_id: str) -> bool:
    """查询用户是否对指定论文有全文访问权限。"""
    try:
        from web.backend.db.base import get_session_factory
        from web.backend.db.models import UserPaper
        from uuid import UUID
        from sqlalchemy import select
        async with get_session_factory()() as db:
            result = await db.execute(
                select(UserPaper).where(
                    UserPaper.user_id == UUID(user_id),
                    UserPaper.paper_id == paper_id,
                    UserPaper.has_fulltext_access.is_(True),
                )
            )
            return result.scalar_one_or_none() is not None
    except Exception:
        return False


async def _can_reuse_paper_pdf(paper: dict, user_id: str | None) -> bool:
    """判断当前用户是否可以复用已有 paper 的 pdf_path。

    - public paper + pdf_path 在公共缓存目录 → 允许
    - public paper + pdf_path 在用户目录 → 需要权限
    - private paper → 仅 owner 或 has_fulltext_access
    - 无 pdf_path → False
    """
    pdf_path = paper.get("pdf_path")
    if not pdf_path:
        return False

    visibility = paper.get("visibility", "public")
    creator = str(paper.get("created_by_user_id") or "")
    paper_id = paper["id"]

    if visibility == "private":
        if not user_id:
            return False
        if creator and creator == str(user_id):
            return True
        return await _user_has_fulltext_access(user_id, paper_id)

    # public paper
    public_dir = Path(_public_papers_dir()).resolve()
    pdf_resolved = Path(pdf_path).resolve()
    if _is_relative_to(pdf_resolved, public_dir):
        return True

    # pdf_path 在某个用户目录下 — 需要权限
    if not user_id:
        return False
    if creator and creator == str(user_id):
        return True
    return await _user_has_fulltext_access(user_id, paper_id)


async def associate_user_paper(
    user_id: str,
    paper_id: str,
    relation_type: str = "parsed",
    has_fulltext_access: bool = True,
    source: str = "paper_parse",
):
    """Create or upgrade a user-paper association in PostgreSQL."""
    if not user_id:
        return
    try:
        from web.backend.db.base import get_session_factory
        from web.backend.db.models import UserPaper
        from uuid import UUID
        from sqlalchemy import select
        async with get_session_factory()() as db:
            result = await db.execute(
                select(UserPaper).where(
                    UserPaper.user_id == UUID(user_id),
                    UserPaper.paper_id == paper_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                if existing.relation_type == "searched" and relation_type != "searched":
                    existing.relation_type = relation_type
                if has_fulltext_access and not existing.has_fulltext_access:
                    existing.has_fulltext_access = True
                if source:
                    existing.source = source
            else:
                db.add(UserPaper(
                    user_id=UUID(user_id),
                    paper_id=paper_id,
                    relation_type=relation_type,
                    has_fulltext_access=has_fulltext_access,
                    source=source,
                ))
            await db.commit()
    except Exception as e:
        logger.warning("Failed to associate user-paper: %s", e)


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


def _milvus_insert(
    paper_id: str,
    chunk_ids: list[int],
    chunks: list[dict],
    embeddings: list[list[float]],
    user_id: str = None,
) -> None:
    """Insert embeddings into Milvus. Silently skips on failure."""
    try:
        from core.vector_store import insert_vectors

        texts = [c["text"] for c in chunks]
        insert_vectors(user_id or DEFAULT_USER_ID, paper_id, chunk_ids, texts, embeddings)
    except Exception as e:
        logger.warning("Failed to insert vectors into Milvus (non-fatal): %s", e)


async def handle_paper_parse(args: dict, user_id: str = None) -> str:
    """解析论文 PDF"""
    paper_id = args.get("paper_id")
    pdf_url = args.get("pdf_url")
    file_path = args.get("file_path")

    # 规范化 paper_id（arXiv ID 统一格式）
    if paper_id:
        paper_id = canonicalize_paper_id(paper_id)

    if not paper_id and not pdf_url and not file_path:
        return fail("paper_parse", "请提供 paper_id、pdf_url 或 file_path。")

    # 公共下载目录（所有用户共享）
    public_dir = _public_papers_dir()
    os.makedirs(public_dir, exist_ok=True)

    # 确定 PDF 来源
    pdf_path = None
    resolved_paper_id = paper_id
    is_local_file = False

    # 本地文件（用户上传）→ 路径安全校验
    if file_path:
        try:
            pdf_path = _validate_user_local_file(file_path, user_id)
        except PermissionError as e:
            return fail("paper_parse", str(e))
        except FileNotFoundError as e:
            return fail("paper_parse", str(e))
        is_local_file = True
        if not resolved_paper_id:
            resolved_paper_id = os.path.splitext(os.path.basename(file_path))[0]

    async with get_connection() as conn:
        # 尝试从数据库获取论文信息
        if paper_id:
            paper = await get_paper(conn, paper_id)
            if paper:
                if paper.get("pdf_path") and os.path.exists(paper["pdf_path"]):
                    if not _can_reuse_paper_pdf(paper, user_id):
                        return fail("paper_parse", "您无权访问该论文的本地 PDF。")
                    pdf_path = str(Path(paper["pdf_path"]).resolve())
                elif paper.get("source") == "arxiv":
                    arxiv_id = paper_id.replace("arxiv:", "")
                    pdf_url = pdf_url or f"https://arxiv.org/pdf/{arxiv_id}"
                elif paper.get("source") == "semantic_scholar":
                    if not pdf_url:
                        pdf_url = _try_get_s2_pdf_url(paper_id)

    # 下载 PDF（URL 来源 → 全局公共缓存）
    if not pdf_path:
        if pdf_url:
            safe_id = (resolved_paper_id or "unknown").replace("/", "_").replace(":", "_")
            pdf_path = os.path.join(public_dir, f"{safe_id}.pdf")
            if not os.path.exists(pdf_path):
                success = await _download_pdf(pdf_url, pdf_path)
                if not success:
                    return fail("paper_parse", f"无法从 {pdf_url} 下载 PDF。")
        else:
            return fail("paper_parse", "无法确定 PDF 来源。请提供 pdf_url。")

    if not os.path.exists(pdf_path):
        return fail("paper_parse", f"PDF 文件不存在: {pdf_path}")

    # 检查是否已经解析过
    async with get_connection() as conn:
        if resolved_paper_id:
            existing_chunks = await get_chunks_by_paper(conn, resolved_paper_id)
            if existing_chunks:
                # 可见性校验：private 论文需要权限才能关联
                paper = await get_paper(conn, resolved_paper_id)
                if paper and paper.get("visibility") == "private":
                    creator = str(paper.get("created_by_user_id") or "")
                    if creator and creator != str(user_id or ""):
                        if not await _user_has_fulltext_access(user_id or "", resolved_paper_id):
                            return fail("paper_parse", f"论文 {resolved_paper_id} 是私有论文，您无权访问。")
                await associate_user_paper(user_id, resolved_paper_id)
                return ok(
                    "paper_parse",
                    {"paper_id": resolved_paper_id, "already_parsed": True, "chunk_count": len(existing_chunks)},
                    summary=f"论文 {resolved_paper_id} 已解析（{len(existing_chunks)} 个分块）",
                    warnings=["如需重新解析，请先删除相关数据"],
                )

    # 解析 PDF
    try:
        if is_local_file:
            # 本地文件用 pymupdf4llm
            from core.pdf_parser import parse_pdf_to_markdown
            markdown_text = parse_pdf_to_markdown(pdf_path)
        else:
            # URL 用 MinerU，保存到公共 papers 目录
            save_dir = os.path.join(public_dir, resolved_paper_id or "unknown")
            result = await parse_pdf_with_mineru(pdf_url, save_dir=save_dir)
            if not result.success:
                return fail("paper_parse", f"MinerU 解析失败 - {result.error}")
            markdown_text = result.markdown
            if result.saved_dir:
                logger.info("MinerU output saved to: %s", result.saved_dir)
    except Exception as e:
        return fail("paper_parse", f"PDF 解析失败 - {str(e)}")

    if not markdown_text or len(markdown_text) < 100:
        return fail("paper_parse", "PDF 解析结果为空或过短。")

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
        return fail("paper_parse", "分块结果为空。")

    # 向量化
    embeddings = None
    try:
        texts = [c["text"] for c in all_chunks]
        embeddings = await embed_batch_async(texts)

        # ── 维度一致性校验 ──
        expected_dim = get_embedding_dim()
        if not embeddings:
            logger.warning("Embedding returned empty list")
            embeddings = None
        elif len(embeddings) != len(all_chunks):
            logger.error(
                "Embedding count mismatch: got %d embeddings for %d chunks. "
                "Refusing to write inconsistent data.", len(embeddings), len(all_chunks)
            )
            embeddings = None
        else:
            # 检查所有向量维度一致
            dims = {len(e) for e in embeddings}
            if len(dims) != 1:
                logger.error(
                    "Inconsistent embedding dimensions within batch: %s. "
                    "Refusing to write inconsistent data.", dims
                )
                embeddings = None
            elif list(dims)[0] != expected_dim:
                actual_dim = list(dims)[0]
                logger.error(
                    "Embedding dimension mismatch: got %d, expected %d "
                    "(provider declares dim=%d). Refusing to write. "
                    "Check DASHSCOPE_API_KEY and EMBEDDING_MODEL.",
                    actual_dim, expected_dim, expected_dim,
                )
                embeddings = None
    except EmbeddingProviderError as e:
        logger.error("No embedding provider available: %s", e)
        return fail("paper_parse", str(e))
    except Exception as e:
        logger.warning("Embedding failed, saving chunks without vectors: %s", e)
        embeddings = None

    # 写入数据库
    async with get_connection() as conn:
        # 确保论文存在
        if resolved_paper_id:
            existing = await get_paper(conn, resolved_paper_id)
            if not existing:
                # 从 PDF 内容提取基本信息
                title = _extract_title_from_markdown(markdown_text)
                paper_data = {
                    "id": resolved_paper_id,
                    "title": title or resolved_paper_id,
                    "authors": [],
                    "abstract": "",
                    "year": None,
                    "source": "parsed",
                    "pdf_path": pdf_path,
                    "url": pdf_url,
                    "citation_count": 0,
                }
                if is_local_file and user_id:
                    paper_data["visibility"] = "private"
                    paper_data["created_by_user_id"] = user_id
                await upsert_paper(conn, paper_data)
            else:
                from web.backend.db.models import Paper
                from sqlalchemy import select
                result = await conn.execute(select(Paper).where(Paper.id == resolved_paper_id))
                existing_row = result.scalar_one_or_none()
                if existing_row:
                    if existing_row.visibility == "private" and existing_row.created_by_user_id:
                        if str(existing_row.created_by_user_id) != str(user_id or ""):
                            if not await _user_has_fulltext_access(user_id or "", resolved_paper_id):
                                return fail("paper_parse", "您无权更新该私有论文的 PDF 路径。")
                    existing_row.pdf_path = pdf_path

        chunk_ids = await insert_chunks(conn, resolved_paper_id, all_chunks)

        if embeddings:
            await insert_embeddings_batch(conn, chunk_ids, embeddings)

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
            await insert_citation(conn, resolved_paper_id, target_id)
            citations_added += 1

    # 同步写入 Milvus（如果可用）
    if embeddings and chunk_ids:
        try:
            _milvus_insert(resolved_paper_id, chunk_ids, all_chunks, embeddings, user_id=user_id)
        except Exception as e:
            # DimensionMismatchError 等已由 _milvus_insert 内部记录
            logger.warning("Milvus insertion failed (non-fatal): %s", e)

    # 同步写入 Elasticsearch（如果可用）
    es_indexed_count = 0
    es_warnings: list[str] = []
    if chunk_ids and resolved_paper_id:
        try:
            from core.elasticsearch_store import bulk_upsert_chunks
            # 获取论文标题
            _es_title = resolved_paper_id
            async with get_connection() as _es_conn:
                _es_paper = await get_paper(_es_conn, resolved_paper_id)
                if _es_paper:
                    _es_title = _es_paper.get("title", resolved_paper_id)
            es_docs = []
            for i, chunk_id in enumerate(chunk_ids):
                sec = all_chunks[i]["section"] if i < len(all_chunks) else ""
                txt = all_chunks[i]["text"] if i < len(all_chunks) else ""
                es_docs.append({
                    "chunk_id": chunk_id,
                    "paper_id": resolved_paper_id,
                    "title": _es_title,
                    "section": sec,
                    "text": txt,
                })
            es_result = await bulk_upsert_chunks(es_docs)
            es_indexed_count = es_result.get("success", 0)
            if es_result.get("errors"):
                es_warnings.extend([f"ES: {e}" for e in es_result["errors"]])
            if es_indexed_count > 0:
                logger.info("ES indexed %d chunks for paper %s", es_indexed_count, resolved_paper_id)
        except Exception as e:
            es_warnings.append(f"Elasticsearch insertion failed: {e}")
            logger.warning("Elasticsearch insertion failed (non-fatal): %s", e)

    # 关联用户与论文（PostgreSQL）
    if resolved_paper_id:
        await associate_user_paper(user_id, resolved_paper_id)

    # 自动构建知识图谱（从摘要提取实体）
    kg_result = ""
    if resolved_paper_id:
        try:
            from tools.knowledge_graph import extract_from_abstract_sync
            kg_result = await extract_from_abstract_sync(resolved_paper_id, user_id=user_id)
            logger.info("Knowledge graph updated for %s", resolved_paper_id)
        except Exception as e:
            logger.warning("Knowledge graph extraction failed: %s", e)

    # 提取标题（供 sources 使用）
    title = None
    async with get_connection() as conn:
        if resolved_paper_id:
            paper_row = await get_paper(conn, resolved_paper_id)
            if paper_row:
                title = paper_row.get("title")

    # 构建返回结果
    sections_preview = [
        {"name": sec["section"], "preview": truncate(sec["text"], MAX_SECTION_PREVIEW)}
        for sec in sections[:MAX_SECTIONS]
    ]
    references_preview = [truncate(ref, MAX_REF_LEN) for ref in refs[:MAX_REFS]]

    # 知识图谱结果处理：尝试解析为 dict，失败则保留原始文本
    kg_stats = {}
    kg_summary_text = ""
    if kg_result:
        try:
            import json
            kg_stats = json.loads(kg_result)
        except (json.JSONDecodeError, TypeError):
            kg_summary_text = kg_result

    data = {
        "paper_id": resolved_paper_id,
        "pdf_path": pdf_path,
        "section_count": len(sections),
        "chunk_count": len(all_chunks),
        "embedding_done": bool(embeddings),
        "citations_count": citations_added,
        "elasticsearch_indexed": es_indexed_count,
        "sections_preview": sections_preview,
        "references_preview": references_preview,
        "kg": kg_stats,
    }
    if kg_summary_text:
        data["kg_summary"] = kg_summary_text

    return ok(
        "paper_parse",
        data,
        summary=f"论文 {resolved_paper_id} 解析完成：{len(sections)} 章节, {len(all_chunks)} 分块, {citations_added} 引用",
        sources=[{"id": resolved_paper_id, "title": title or resolved_paper_id}],
        warnings=es_warnings,
    )


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
