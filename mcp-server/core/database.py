"""SQLite 数据库管理 - 论文元数据、分块、向量、引用关系"""

import json
import os
import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("research-server.db")

DB_PATH = os.environ.get("RESEARCH_DB_PATH", os.path.join(
    os.environ.get("RESEARCH_DATA_DIR", "./data"), "research.db"
))


def get_db_path() -> str:
    return DB_PATH


def init_db(db_path: Optional[str] = None) -> None:
    """初始化数据库表结构"""
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                authors TEXT,
                abstract TEXT,
                year INTEGER,
                source TEXT,
                pdf_path TEXT,
                url TEXT,
                citation_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id TEXT REFERENCES papers(id),
                section TEXT,
                ordinal INTEGER,
                text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS embeddings (
                chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id),
                dim INTEGER NOT NULL,
                vec BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS citations (
                source_id TEXT REFERENCES papers(id),
                target_id TEXT REFERENCES papers(id),
                PRIMARY KEY (source_id, target_id)
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
            CREATE INDEX IF NOT EXISTS idx_citations_source ON citations(source_id);
            CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_id);
        """)
        conn.commit()
        logger.info("Database initialized at %s", path)
    finally:
        conn.close()


@contextmanager
def get_connection(db_path: Optional[str] = None):
    """获取数据库连接的上下文管理器"""
    path = db_path or DB_PATH
    # 如果数据库不存在，先初始化
    if not os.path.exists(path):
        init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Paper CRUD ────────────────────────────────────────────────────────────

def upsert_paper(conn: sqlite3.Connection, paper: dict) -> None:
    """插入或更新论文元数据"""
    conn.execute("""
        INSERT INTO papers (id, title, authors, abstract, year, source, pdf_path, url, citation_count)
        VALUES (:id, :title, :authors, :abstract, :year, :source, :pdf_path, :url, :citation_count)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            authors=excluded.authors,
            abstract=excluded.abstract,
            year=excluded.year,
            source=COALESCE(excluded.source, papers.source),
            pdf_path=COALESCE(excluded.pdf_path, papers.pdf_path),
            url=COALESCE(excluded.url, papers.url),
            citation_count=MAX(papers.citation_count, excluded.citation_count)
    """, {
        "id": paper["id"],
        "title": paper["title"],
        "authors": json.dumps(paper.get("authors", []), ensure_ascii=False),
        "abstract": paper.get("abstract"),
        "year": paper.get("year"),
        "source": paper.get("source"),
        "pdf_path": paper.get("pdf_path"),
        "url": paper.get("url"),
        "citation_count": paper.get("citation_count", 0),
    })


def get_paper(conn: sqlite3.Connection, paper_id: str) -> Optional[dict]:
    """获取单篇论文"""
    row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row:
        return dict(row)
    return None


def get_all_papers(conn: sqlite3.Connection) -> list[dict]:
    """获取所有论文"""
    rows = conn.execute("SELECT * FROM papers ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ── Chunk CRUD ────────────────────────────────────────────────────────────

def insert_chunks(conn: sqlite3.Connection, paper_id: str, chunks: list[dict]) -> list[int]:
    """批量插入分块，返回 chunk_id 列表"""
    ids = []
    for chunk in chunks:
        cursor = conn.execute("""
            INSERT INTO chunks (paper_id, section, ordinal, text)
            VALUES (?, ?, ?, ?)
        """, (paper_id, chunk.get("section"), chunk.get("ordinal", 0), chunk["text"]))
        ids.append(cursor.lastrowid)
    return ids


def get_chunks_by_paper(conn: sqlite3.Connection, paper_id: str) -> list[dict]:
    """获取论文的所有分块"""
    rows = conn.execute(
        "SELECT * FROM chunks WHERE paper_id=? ORDER BY ordinal", (paper_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_chunks(conn: sqlite3.Connection) -> list[dict]:
    """获取所有分块"""
    rows = conn.execute("SELECT * FROM chunks ORDER BY paper_id, ordinal").fetchall()
    return [dict(r) for r in rows]


# ── Embedding CRUD ────────────────────────────────────────────────────────

def insert_embedding(conn: sqlite3.Connection, chunk_id: int, vec: list[float]) -> None:
    """插入分块向量"""
    import numpy as np
    arr = np.array(vec, dtype=np.float32)
    conn.execute("""
        INSERT OR REPLACE INTO embeddings (chunk_id, dim, vec)
        VALUES (?, ?, ?)
    """, (chunk_id, len(arr), arr.tobytes()))


def insert_embeddings_batch(conn: sqlite3.Connection, chunk_ids: list[int], vecs: list[list[float]]) -> None:
    """批量插入向量"""
    import numpy as np
    for chunk_id, vec in zip(chunk_ids, vecs):
        arr = np.array(vec, dtype=np.float32)
        conn.execute("""
            INSERT OR REPLACE INTO embeddings (chunk_id, dim, vec)
            VALUES (?, ?, ?)
        """, (chunk_id, len(arr), arr.tobytes()))


def get_all_embeddings(conn: sqlite3.Connection) -> list[dict]:
    """获取所有向量（用于 cosine similarity 检索）"""
    rows = conn.execute("""
        SELECT e.chunk_id, e.dim, e.vec, c.text, c.section, c.paper_id, p.title
        FROM embeddings e
        JOIN chunks c ON e.chunk_id = c.id
        JOIN papers p ON c.paper_id = p.id
    """).fetchall()
    results = []
    import numpy as np
    for row in rows:
        vec = np.frombuffer(row["vec"], dtype=np.float32).copy()
        results.append({
            "chunk_id": row["chunk_id"],
            "dim": row["dim"],
            "vec": vec,
            "text": row["text"],
            "section": row["section"],
            "paper_id": row["paper_id"],
            "title": row["title"],
        })
    return results


# ── Citation CRUD ─────────────────────────────────────────────────────────

def insert_citation(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
    """插入引用关系（忽略外键约束失败）"""
    try:
        conn.execute("""
            INSERT OR IGNORE INTO citations (source_id, target_id)
            VALUES (?, ?)
        """, (source_id, target_id))
    except sqlite3.IntegrityError:
        pass  # 引用的论文不在数据库中，跳过


def get_citations(conn: sqlite3.Connection, paper_id: str) -> dict:
    """获取论文的引用关系"""
    citing = conn.execute(
        "SELECT target_id FROM citations WHERE source_id=?", (paper_id,)
    ).fetchall()
    cited_by = conn.execute(
        "SELECT source_id FROM citations WHERE target_id=?", (paper_id,)
    ).fetchall()
    return {
        "citing": [r["target_id"] for r in citing],
        "cited_by": [r["source_id"] for r in cited_by],
    }
