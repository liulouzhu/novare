# 多用户安全修复记录

> 修复日期：2026-06-10
> 涉及提交：`d3915dc`、`9ced35d`、`e358724`

---

## 一、上传端点无认证 + 路径穿越

### 问题

`POST /api/upload` 没有任何认证，且文件直接保存到全局 `workspace/uploads/` 目录：

- 任何匿名用户都能上传文件
- 不同用户的文件混在同一个目录，文件名直接使用用户提供的原始名称
- 文件名未过滤，存在路径穿越风险（如 `../../etc/passwd`）
- 同名文件会互相覆盖

### 修复方案

**文件**：`web/backend/routes/upload.py`

1. **加认证**：注入 `get_current_user` 依赖，未登录返回 401
2. **目录隔离**：保存路径从 `uploads/` 改为 `uploads/<user_id>/`，每个用户独立目录
3. **文件名安全处理**：
   - `Path(name).name` 剥离目录部分，防止 `../../` 穿越
   - `re.sub(r"[^\w.\-]", "_", ...)` 只保留字母数字和常见符号
   - 文件名前拼接 `uuid.uuid4().hex`，彻底避免冲突

---

## 二、PDF 端点无认证读取私有文件

### 问题

`GET /api/papers/pdf/view` 明确标注"无需认证"。对于 arxiv 等公开论文这没问题，但如果 PDF 是用户上传的私有论文，任何人都能通过 `paper_id` 直接下载别人的文件。

### 修复方案

**文件**：`web/backend/routes/papers.py`

采用**可选认证 + 按需鉴权**策略：

1. 新增 `_try_get_user()` 可选认证依赖——有 token 解析用户，没有返回 `None`，不自动报错
2. **本地 PDF 文件**：要求登录（401）+ 三重所有权校验（403）：
   - `paper.visibility == "public"`（公开论文任何人可看）
   - `paper.created_by_user_id == user.id`（论文创建者）
   - `user_paper_repo.has_parsed(paper_id)`（解析过该论文的用户）
3. **外部重定向**（arxiv / Semantic Scholar）：保持无需认证，因为这些 PDF 本身就是公开资源

---

## 三、Chunks 端点缺少所有权校验

### 问题

`GET /api/papers/{paper_id}/chunks` 虽然需要登录，但只按 `paper_id` 查询 chunks，没有验证当前用户是否拥有这篇论文。任何登录用户都能查看任意论文的全文分块。

### 修复方案

**文件**：`web/backend/routes/papers.py`

在查询 chunks 之前加入与 PDF 端点相同的三重所有权校验：

```python
is_owner = (
    paper.visibility == "public"
    or paper.created_by_user_id == user.id
    or user_paper_repo.has_parsed(paper_id)
)
```

---

## 四、Paper 缺少可见性模型

### 问题

Paper 表是全局共享的，所有论文对所有用户可见。对于 arxiv / Semantic Scholar 的公开论文这合理，但对于用户上传的私有 PDF，无法区分"谁能看"。

### 修复方案

#### 4.1 数据模型

**文件**：`web/backend/db/models.py`

Paper 表新增两个字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `visibility` | `String(10)` | `public`（默认）或 `private` |
| `created_by_user_id` | `UUID` | 创建者用户 ID，外键关联 `users.id` |

#### 4.2 数据库迁移

**文件**：`web/backend/db/migrations/versions/c4d5e6f7a8b9_add_paper_visibility.py`

- 添加两列 + CHECK 约束 + 索引
- 向下兼容：已有数据默认 `visibility='public'`

#### 4.3 写入侧——谁创建论文就打什么标签

| 来源 | visibility | created_by_user_id |
|---|---|---|
| `paper_search`（arxiv / S2） | `public` | `NULL` |
| `innovation_search`（创新搜索） | `public` | `NULL` |
| `paper_parse`（本地上传 PDF） | `private` | 当前用户 ID |
| `paper_parse`（URL 下载） | 继承已有 | 继承已有 |

**关键逻辑**（`mcp-server/core/database.py` 的 `upsert_paper`）：已有 `private` 论文不会被降级为 `public`，防止越权覆盖。

#### 4.4 读取侧——谁能看

**`PaperRepository`**（`web/backend/repositories/paper_repo.py`）新增：

- `get_visible(paper_id, user_id)`：只返回用户有权访问的论文
- `get_all(q, user_id)`：`public` 全部可见，`private` 仅创建者可见

---

## 五、搜索论文对所有用户可见

### 问题

`paper_search` 搜索到的论文以 `visibility='public'` 写入数据库，但没有和搜索用户关联（不创建 `UserPaper` 记录）。导致：

- 用户搜索了论文但列表里看不到（没有 UserPaper 关联）
- 其他用户反而能看到别人搜的论文（public 全局可见）

### 修复方案

#### 5.1 搜索时自动关联用户

**文件**：`mcp-server/research_server.py`、`mcp-server/tools/paper_search.py`、`mcp-server/tools/innovation_search.py`

- `research_server.py` 将提取到的 `user_id` 传给 `handle_paper_search`
- 搜索完成后自动调用 `associate_user_paper(user_id, paper_id)` 创建 UserPaper 记录
- `innovation_search` 做同样处理

#### 5.2 列表只返回用户关联的论文

**文件**：`web/backend/routes/papers.py` 的 `list_papers`

改为先查 `UserPaper` 获取当前用户的论文 ID 集合，再过滤结果——用户只能看到自己搜索过或解析过的论文。

### 最终数据流

```
paper_search ──→ Paper 表 (shared, visibility=public)
                 └──→ UserPaper (user_id, paper_id)  ← 新增

paper_parse  ──→ Paper 表 (shared 或更新)
                 └──→ UserPaper (user_id, paper_id)  ← 已有

list_papers  ──→ 只返回 UserPaper 中有记录的论文
```

论文数据集中存储不重复，每个用户只看到自己交互过的论文。

---

## 六、用户文件路径未对齐 user workspace

### 问题

`get_user_workspace(user_id)` 已经能创建用户独立目录（`workspace/<user_id>/`），WebSocket session 也按用户 workspace 加载。但以下组件的文件路径没有跟这个模型对齐：

- **upload 路由**：通过 `agent_service.config.workspace / "uploads" / user.id` 拼路径，依赖 `agent_service` 全局实例
- **paper_parse 下载 PDF**：存到全局 `./data/papers/`，所有用户的 PDF 混在一起
- **paper_parse MinerU 输出**：同样存到全局目录

### 修复方案

#### upload 路由

**文件**：`web/backend/routes/upload.py`

去掉 `agent_service` 依赖，直接调用 `get_user_workspace(user.id)` 获取用户目录：

```python
upload_dir = Path(get_user_workspace(str(user.id))) / "uploads"
```

#### paper_parse PDF 存储

**文件**：`mcp-server/tools/paper_parse.py`

新增 `_papers_dir(user_id)` 辅助函数，按用户计算存储路径：

```python
def _papers_dir(user_id: str | None = None) -> str:
    if user_id:
        from novare.config import get_user_workspace
        return os.path.join(get_user_workspace(user_id), "papers")
    return _GLOBAL_PAPERS_DIR  # 兜底：全局 ./data/papers/
```

所有 PDF 下载和 MinerU 输出统一走 `_papers_dir(user_id)`。

### 修复后目录结构

```
workspace/
└── <user_id>/                      ← get_user_workspace() 统一入口
    ├── .novare/sessions/           ✓ WebSocket session
    ├── uploads/                    ✓ 上传文件
    └── papers/                     ✓ 论文 PDF 存储
```

---

## 七、Skill 目录未区分公共与私有

### 问题

所有 skill 文件存放在 `workspace/.novare/skills/`，这是全局共享路径。多用户场景下：

- 系统内置 skill 和用户自定义 skill 混在同一目录
- 用户无法创建私有 skill（会和其他用户冲突）
- 没有"公共 skill + 私有 skill 覆盖"的分层机制

### 修复方案

#### 目录分层

| 目录 | 用途 | 优先级 |
|---|---|---|
| `workspace/<user_id>/.novare/skills/` | 用户私有 skill | 最高（可覆盖系统默认） |
| `system/skills/` | 系统公共 skill | 中 |
| `~/.novare/skills/` | 用户 home 目录 skill | 最低兜底 |

#### config.py

**文件**：`novare/config.py`

默认 skill_dirs 从 `workspace/.novare/skills` 改为 `system/skills`：

```python
cfg.skill_dirs = [
    project_root / "system" / "skills",
    Path.home() / ".novare" / "skills",
]
```

#### cli.py

**文件**：`novare/cli.py`

启动时读取 `NOVARE_USER_ID` 环境变量，有则将用户私有目录插入最高优先级：

```python
skill_dirs = list(config.skill_dirs)
user_id = os.environ.get("NOVARE_USER_ID")
if user_id:
    user_skill_dir = Path(get_user_workspace(user_id)) / ".novare" / "skills"
    skill_dirs.insert(0, user_skill_dir)  # 用户私有优先
skills = discover_skills(skill_dirs)
```

#### Skill 文件迁移

5 个公共 skill 从 `workspace/.novare/skills/` 迁移到 `system/skills/`：`ask.md`、`compile.md`、`innovation.md`、`parse.md`、`research.md`。

---

## 涉及文件清单

| 文件 | 改动类型 |
|---|---|
| `web/backend/routes/upload.py` | 修改：认证 + 目录隔离 + 文件名安全 + 统一 workspace |
| `web/backend/routes/papers.py` | 修改：可选认证 + 可见性过滤 + 所有权校验 |
| `web/backend/db/models.py` | 修改：Paper 新增 visibility / created_by_user_id |
| `web/backend/db/migrations/versions/c4d5e6f7a8b9_*.py` | 新增：Alembic 迁移 |
| `web/backend/repositories/paper_repo.py` | 修改：新增 get_visible + 可见性过滤 |
| `mcp-server/core/database.py` | 修改：upsert_paper 支持新字段 |
| `mcp-server/research_server.py` | 修改：paper_search 传 user_id |
| `mcp-server/tools/paper_search.py` | 修改：接收 user_id + 自动关联 |
| `mcp-server/tools/paper_parse.py` | 修改：本地 PDF 标记 private + 所有者 + 用户 workspace |
| `mcp-server/tools/innovation_search.py` | 修改：搜索后自动关联用户 |
| `novare/config.py` | 修改：默认 skill_dirs 改为 system/skills |
| `novare/cli.py` | 修改：支持 NOVARE_USER_ID 加载用户私有 skill |
| `system/skills/*.md` | 新增：从 workspace/.novare/skills/ 迁移的公共 skill |
