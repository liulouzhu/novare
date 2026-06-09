# 多用户 RAG 隔离漏洞报告：user_id 在向量操作中被丢弃

## 漏洞概述

**严重程度**: 高  
**影响范围**: 所有 `paper_parse` 和 `rag_query` 工具调用  
**发现日期**: 2026-06-09  
**修复日期**: 2026-06-09

Web 层正确传递了用户身份，`user_id` 经过 JWT → WebSocket → AgentService → AgentLoop → ToolRegistry → MCP Client → MCP Server 完整链路到达工具处理函数。但在最后一英里——调用 Milvus 向量库操作时——被替换为硬编码的 `DEFAULT_USER_ID`（值为 `"default"`），导致所有用户的向量数据混存在同一个分区中。

---

## 调用链路分析

```
✅ JWT 解码 → user_id_str          (chat.py:41)
✅ AgentService.run_turn(user_id)   (agent_service.py:229)
✅ tool_context={"user_id": ...}    (agent_service.py:229)
✅ AgentLoop → ToolRegistry.execute  (agent_loop.py:108)
✅ _make_mcp_handler → payload["_user_id"]  (agent_service.py:300)
✅ MCP stdio → arguments["_user_id"]        (mcp_client.py:66)
✅ research_server.py → arguments.pop("_user_id")  (research_server.py:180)
✅ handle_paper_parse(args, user_id=user_id)       (research_server.py:192)
✅ handle_rag_query(args, user_id=user_id)         (research_server.py:196)

❌ _milvus_insert() 使用 DEFAULT_USER_ID  ← 这里断了
❌ _milvus_search() 使用 DEFAULT_USER_ID  ← 这里也断了
❌ _brute_force_search() 无任何用户过滤   ← 完全没隔离
```

---

## 问题详情

### Bug #1：Milvus 插入时 user_id 丢失

**文件**: `mcp-server/tools/paper_parse.py`

`_milvus_insert()` 函数不接受 `user_id` 参数，内部硬编码使用 `DEFAULT_USER_ID`：

```python
# 修复前 (line 73-86)
def _milvus_insert(paper_id, chunk_ids, chunks, embeddings):
    insert_vectors(DEFAULT_USER_ID, paper_id, ...)  # ← 所有用户都写入 "default" 分区

# handle_paper_parse 中的调用 (line 250)
_milvus_insert(resolved_paper_id, chunk_ids, all_chunks, embeddings)  # ← user_id 没传
```

**后果**: 用户 A 解析的论文向量写入 `"default"` 分区，用户 B 的检索会命中这些向量。

### Bug #2：Milvus 检索时 user_id 丢失

**文件**: `mcp-server/tools/rag_query.py`

`handle_rag_query` 接收到正确的 `user_id`，但调用搜索时使用了 `DEFAULT_USER_ID`：

```python
# 修复前 (line 102)
top_results = _milvus_search(query_vec_list, top_k, DEFAULT_USER_ID)  # ← 应传 user_id
```

**后果**: 所有用户的检索都查询 `"default"` 分区，跨用户数据完全混在一起。

### Bug #3：暴力搜索回退无用户隔离

**文件**: `mcp-server/tools/rag_query.py`

当 Milvus 不可用时，`_brute_force_search()` 直接从 SQLite 读取所有 embedding，无任何用户过滤：

```python
# 修复前 (line 26-49)
def _brute_force_search(query_vec, top_k):  # ← 无 user_id 参数
    all_embeddings = get_all_embeddings(conn)  # ← 返回所有用户的 embedding
    # 无任何 user_id 过滤逻辑
```

**后果**: SQLite 回退路径下，任何用户可以检索到所有其他用户的论文数据。

### Bug #4（已知，未在本次修复范围）：SQLite 无 user_id 列

`database.py` 中的 `papers`、`chunks`、`embeddings` 表均无 `user_id` 列。SQLite 作为全局元数据存储，用户关联仅在 PostgreSQL 的 `user_papers` 表中。暴力搜索的隔离通过查询 PostgreSQL 获取用户关联论文 ID 后在 Python 层过滤实现。

---

## 修复方案

### 修改的文件

| 文件 | 修改内容 |
|---|---|
| `mcp-server/tools/paper_parse.py` | `_milvus_insert()` 增加 `user_id` 参数，调用处传入真实 `user_id` |
| `mcp-server/tools/rag_query.py` | Milvus 搜索传入真实 `user_id`；暴力搜索增加基于 PostgreSQL 的用户论文过滤 |

### 修复 1：paper_parse.py

**`_milvus_insert` 增加 `user_id` 参数：**

```python
# 修复后
def _milvus_insert(
    paper_id: str,
    chunk_ids: list[int],
    chunks: list[dict],
    embeddings: list[list[float]],
    user_id: str = None,          # ← 新增参数
) -> None:
    texts = [c["text"] for c in chunks]
    insert_vectors(user_id or DEFAULT_USER_ID, paper_id, chunk_ids, texts, embeddings)
```

**调用处传递 `user_id`：**

```python
# 修复后 (line 250)
_milvus_insert(resolved_paper_id, chunk_ids, all_chunks, embeddings, user_id=user_id)
```

### 修复 2：rag_query.py

**Milvus 搜索使用真实 `user_id`：**

```python
# 修复前
top_results = _milvus_search(query_vec_list, top_k, DEFAULT_USER_ID)

# 修复后
top_results = _milvus_search(query_vec_list, top_k, user_id or DEFAULT_USER_ID)
```

**暴力搜索增加用户论文过滤：**

新增 `_get_user_paper_ids()` 辅助函数，从 PostgreSQL 查询用户关联的论文 ID 集合：

```python
def _get_user_paper_ids(user_id: str) -> set[str] | None:
    """从 PostgreSQL 获取用户关联的论文 ID 集合"""
    if not user_id:
        return None
    db = SessionLocal()
    return {
        str(up.paper_id)
        for up in db.query(UserPaper.paper_id)
        .filter(UserPaper.user_id == UUID(user_id))
        .all()
    }
```

`_brute_force_search()` 增加 `user_id` 参数，在遍历 embedding 时过滤：

```python
def _brute_force_search(query_vec, top_k, user_id=None):
    allowed_paper_ids = _get_user_paper_ids(user_id)
    if user_id and allowed_paper_ids is not None and not allowed_paper_ids:
        return []  # 用户无关联论文

    all_embeddings = get_all_embeddings(conn)
    for emb in all_embeddings:
        if allowed_paper_ids is not None and emb["paper_id"] not in allowed_paper_ids:
            continue  # ← 跳过非该用户的论文
        ...
```

---

## 修复前后对比

| 操作 | 修复前 | 修复后 |
|---|---|---|
| Milvus 插入 | `insert_vectors("default", ...)` | `insert_vectors(user_id, ...)` |
| Milvus 检索 | `search_vectors("default", ...)` | `search_vectors(user_id, ...)` |
| 暴力搜索回退 | 无过滤，返回所有用户数据 | 通过 PostgreSQL `user_papers` 过滤，只返回用户关联论文 |
| user_id 传递 | 在 `_milvus_insert` 入口断开 | 完整传递到 Milvus 层 |

---

## 隔离架构示意

```
                    PostgreSQL                      Milvus
                    ┌──────────────┐          ┌──────────────────┐
 user_id ────────►  │ user_papers  │          │  partition_key   │
                    │ user_id ↔    │          │  = user_id       │
                    │ paper_id     │          │                  │
                    └──────┬───────┘          │  每个用户独立分区  │
                           │                  └──────────────────┘
                           │                         ▲
                           │  暴力搜索过滤            │ Milvus 搜索
                           ▼                         │
                    ┌──────────────────┐             │
                    │ SQLite           │             │
                    │ papers/chunks/   │─────────────┘
                    │ embeddings       │  (插入时写入用户分区)
                    │ (无 user_id 列)  │
                    └──────────────────┘
```

**双层隔离策略：**
- **主路径（Milvus）**: `user_id` 作为 `partition_key`，物理分区隔离，查询时自动过滤
- **回退路径（暴力搜索）**: 通过 PostgreSQL `user_papers` 表获取用户关联论文 ID，在 Python 层过滤

---

## 验证建议

1. **Milvus 插入隔离**：用户 A 解析论文 → 检查 Milvus 中 `user_id` 字段为 A 的 UUID，非 `"default"`
2. **Milvus 检索隔离**：用户 B 检索 → 不应命中用户 A 独有解析的论文
3. **暴力搜索隔离**：禁用 Milvus → 用户 A 检索 → 只返回 A 关联的论文分块
4. **向后兼容**：MCP CLI 直接调用（无 user_id）→ 回退到 `DEFAULT_USER_ID`，行为不变
