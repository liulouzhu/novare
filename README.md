# Novare 多用户智能科研平台

## 项目介绍

Novare 是一个面向科研场景的多用户智能体平台。它以大语言模型为推理核心，通过 Agent 循环协调论文搜索与解析、RAG 语义检索、知识图谱、创新点检索、代码执行和子智能体协作等能力，并同时提供 Web、命令行及消息渠道等多种使用入口。

项目整体采用分层架构，上层负责用户交互和业务接入，中间层负责任务编排与科研能力扩展，底层负责数据持久化和安全执行：

```text
Web / CLI / 消息渠道
        ↓
API 与应用服务层
        ↓
Agent 核心编排层
        ↓
能力扩展与科研工具层
        ↓
数据与基础设施层
```

### 项目分层

| 层级 | 主要目录 | 职责 |
| --- | --- | --- |
| 1. 交互与接入层 | `web/frontend/`、`novare/cli.py`、`novare/channels/` | 提供 React Web 界面、命令行 REPL 和微信等消息渠道入口，负责展示对话、工具调用、任务状态、论文库与知识图谱。 |
| 2. API 与应用服务层 | `web/backend/routes/`、`web/backend/auth/`、`web/backend/agent_service.py` | 基于 FastAPI 提供认证、会话、聊天、论文、记忆、上传和知识图谱 API，并通过 WebSocket 桥接 Agent 的流式输出。 |
| 3. Agent 核心编排层 | `novare/agent_loop.py`、`novare/llm_client.py`、`novare/context_manager.py`、`novare/session.py` | 管理“模型推理 → 工具调用 → 结果回填”的循环，处理上下文压缩、会话状态、任务状态、超时与取消。 |
| 4. 能力扩展与科研工具层 | `novare/tools/`、`novare/subagents/`、`novare/skill.py`、`system/skills/`、`mcp-server/` | 通过工具注册表、Skills、子智能体和 MCP 扩展 Agent 能力，将论文搜索、PDF 解析、RAG 检索、知识图谱、创新点检索和代码执行封装为可动态发现的科研工具，并支持任务委派与可复用科研工作流。 |
| 5. 数据与基础设施层 | `web/backend/db/`、`web/backend/repositories/`、`mcp-server/core/`、`web/backend/redis_service.py`、`docker/sandbox/` | 负责用户、会话、消息、论文和记忆的持久化，维护向量索引与论文数据；Redis 提供并发锁和任务状态，Docker 沙箱隔离代码执行。 |

这种分层方式使交互入口、Agent 编排和科研工具彼此解耦：新增前端或消息渠道时可以复用同一套 Agent 服务，新增科研能力时则可以通过 Skill、内置工具或 MCP 工具接入，而无需修改核心对话循环。

## 快速开始

### 1. 环境要求

- Python 3.10 或更高版本
- Node.js 18 或更高版本
- PostgreSQL（生产环境）
- Docker（可选，用于隔离代码执行）
- Redis（可选，用于并发锁、消息去重和任务状态）
- Milvus（推荐，用于论文向量召回）
- Elasticsearch 8.x（推荐，用于 BM25 关键词召回）

> **测试环境**：测试套件由 `tests/conftest.py` 显式设置 `sqlite+aiosqlite:///:memory:` 作为数据库 URL，无需安装 PostgreSQL。直接运行 `python -m pytest -q` 即可。

### 2. 安装后端依赖

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install -r web\backend\requirements.txt
python -m pip install "elasticsearch[async]>=8.12,<9"
```

Linux 或 macOS 使用以下命令激活虚拟环境：

```bash
source .venv/bin/activate
```

### 3. 安装前端依赖

```powershell
cd web\frontend
npm install
cd ..\..
```

### 4. 配置环境变量

复制 `.env.example` 为 `.env`，至少配置模型服务和 PostgreSQL：

```dotenv
NOVARE_API_KEY=your-api-key
NOVARE_BASE_URL=https://api.minimax.chat/v1
NOVARE_MODEL=MiniMax-Text-01

DATABASE_URL=postgresql://postgres:your-password@localhost:5432/research_agent
JWT_SECRET_KEY=replace-with-a-long-random-string
```

`NOVARE_BASE_URL` 支持 OpenAI 兼容接口，可以将地址、密钥和模型名替换为实际使用的模型服务。使用前请确保 `DATABASE_URL` 指向的数据库已经创建。

> **URL 格式说明**：传统的 `postgresql://` URL 会在应用内部自动转换为 `postgresql+asyncpg://`。`postgresql+asyncpg://` 格式可直接使用。生产环境必须配置 `DATABASE_URL`，缺少时应用启动会报错。

#### 自进化流程与参数

自进化有两种触发来源：任务失败或停滞时由 Reflexion 归纳修正经验；复杂任务成功后由后台 reviewer 总结可复用工作流。经验经过脱敏后按独立会话聚合，默认至少在 3 个不同会话中形成一致证据，才会成为 `supported` 候选。候选可以生成已有 Skill 的 patch，也可以新建 Skill；自动评测通过后默认备份并写入，开启写入审批后则停在草稿状态等待用户批准，所有写入都保留审计、版本归因和回滚能力。

```text
失败/停滞 ──> Reflexion ──> ReflectionResolution ──> 失败经验候选 ──┐
                                                                    ├─> Skill patch/create 提议
复杂任务成功 ──> 后台工作流总结 ──> 成功经验候选 ──────────────────┘
                                                                         │
                    回滚 <── 应用 <──（可选用户批准）<── 自动评测门禁 <──┘
```

相关参数可在 `.env` 中配置：

```dotenv
# 失败反思与经验观察
NOVARE_REFLEXION_ENABLED=false
NOVARE_EVOLUTION_OBSERVE_ENABLED=false
NOVARE_EVOLUTION_MIN_CONFIDENCE=0.6
NOVARE_EVOLUTION_MIN_INDEPENDENT_SESSIONS=3

# 复杂任务成功后的工作流沉淀
NOVARE_EVOLUTION_SUCCESS_ENABLED=true
NOVARE_EVOLUTION_SUCCESS_MIN_TOOL_CALLS=5
NOVARE_EVOLUTION_SUCCESS_MIN_UNIQUE_TOOLS=3
NOVARE_EVOLUTION_SUCCESS_MIN_ITERATIONS=4
NOVARE_EVOLUTION_SUCCESS_REQUIRE_VERIFICATION=false
NOVARE_EVOLUTION_SUCCESS_MIN_CONFIDENCE=0.7
NOVARE_EVOLUTION_SUCCESS_MAX_TOKENS=1800

# Skill diff、新建 Skill 与自动评测门禁
NOVARE_EVOLUTION_PROPOSAL_ENABLED=false
NOVARE_EVOLUTION_AUTO_PROMOTE=true
NOVARE_EVOLUTION_WRITE_APPROVAL=false
NOVARE_EVOLUTION_SKILL_MAX_BYTES=15360
NOVARE_EVOLUTION_PROPOSAL_MAX_TOKENS=4000
NOVARE_EVOLUTION_EVAL_MAX_TOKENS=3000
NOVARE_EVOLUTION_EVAL_MIN_DELTA=0.05

# 提议模式需要独立 reviewer
NOVARE_REVIEWER_API_KEY=your-reviewer-api-key
NOVARE_REVIEWER_BASE_URL=https://your-reviewer-endpoint/v1
NOVARE_REVIEWER_MODEL=your-reviewer-model
```

更新后执行数据库迁移：

```powershell
python -m alembic upgrade head
```


如果要启用论文混合检索与 Qwen3 rerank，再加入以下配置：

```dotenv
# 百炼 Embedding；Qwen3 rerank 默认复用这个 API Key
DASHSCOPE_API_KEY=your-dashscope-api-key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4

# Milvus 向量召回
MILVUS_HOST=localhost
MILVUS_PORT=19530

# Elasticsearch BM25 召回
ELASTICSEARCH_URL=http://localhost:9200
ELASTICSEARCH_INDEX=paper_chunks
# ELASTICSEARCH_USERNAME=
# ELASTICSEARCH_PASSWORD=

# 混合召回与 RRF
RAG_VECTOR_TOP_N=50
RAG_KEYWORD_TOP_N=50
RAG_RRF_K=60

# Qwen3 rerank：RRF 前 20 条重排后再返回用户请求的 top_k
RAG_RERANK_ENABLED=true
RAG_RERANK_MODEL=qwen3-rerank
RAG_RERANK_URL=https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
RAG_RERANK_CANDIDATES=20
RAG_RERANK_TIMEOUT=30
RAG_RERANK_MAX_DOC_CHARS=6000
# 如需单独的 rerank Key，可设置 RAG_RERANK_API_KEY

# CLI 模式执行 RAG 时必须指定有论文权限的用户 UUID
# NOVARE_USER_ID=00000000-0000-0000-0000-000000000000
```

### 5. 启动 Elasticsearch

本地开发可以启动一个关闭安全认证的单节点 Elasticsearch。生产环境不要关闭安全认证，并应通过 `ELASTICSEARCH_USERNAME` 和 `ELASTICSEARCH_PASSWORD` 配置访问凭据。

```powershell
docker run -d --name novare-es -p 9200:9200 -e "discovery.type=single-node" -e "xpack.security.enabled=false" -e "ES_JAVA_OPTS=-Xms1g -Xmx1g" docker.elastic.co/elasticsearch/elasticsearch:8.19.3
```

验证服务：

```powershell
curl http://localhost:9200
```

### 6. 数据库迁移

首次使用或更新后，需要执行 Alembic 迁移：

```powershell
alembic upgrade head
```

### 7. 启动 Web 应用

Windows 可以使用一键启动脚本：

```powershell
.\web\start.bat
```

Linux 或 macOS 可以运行：

```bash
bash web/start.sh
```

也可以分别启动后端和前端：

```powershell
# 终端 1：启动 FastAPI 后端
python -m uvicorn web.backend.app:app --host 0.0.0.0 --port 8000 --reload

# 终端 2：启动 Vite 前端
cd web\frontend
npm run dev
```

启动完成后访问：

- Web 界面：<http://localhost:5173>
- API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

### 8. 启动命令行模式

如果只需要使用命令行科研助手，可以在项目根目录执行：

```powershell
.\.venv\Scripts\Activate.ps1
python -m novare
```

首次启动时，系统会自动发现 `system/skills/` 中的 Skills，并通过 MCP 加载论文搜索、论文解析、RAG、知识图谱和代码执行等科研工具。


## 多用户管理策略

Novare 从身份认证、数据、文件和任务四个层面隔离用户。

### 1. 身份认证

- 密码使用 bcrypt 哈希保存
- 登录后签发有效期 24 小时的 JWT
- HTTP 和 WebSocket 都从 JWT 获取用户 ID
- 未认证、Token 失效或用户停用时拒绝访问

### 2. 数据隔离

数据库查询默认携带 `user_id`，会话、消息、记忆和上传文件只能由所属用户访问。论文元数据可以共享，但用户通过 `UserPaper` 单独管理全文访问权限。

### 3. 文件隔离

每个用户使用独立的 `workspace/<user_id>/` 目录，上传文件、会话文件和工具产物都限制在对应用户目录中。

### 4. 任务隔离

- Redis 锁和任务状态使用 `user_id + session_id` 作为 Key
- TaskState 在每次 `run_turn` 内独立创建
- MCP 工具会收到当前用户 ID
- RAG 只检索用户有全文权限的论文
- 用户画像和情景记忆均按用户隔离

生产环境必须固定设置 `JWT_SECRET_KEY`，否则服务重启后已有 Token 会失效。


## 上下文管理

Novare 使用“完整用户轮次 + Token 预算”的方式管理上下文。

### 1. Token 估计

Token 使用启发式方式估算：

- 中文字符数 × 0.7
- 其他字符数 × 0.25
- 每条消息约 10 tokens 固定开销
- `tool_calls` 中工具名称和参数的 Token 估算

该结果用于判断上下文是否需要压缩，不依赖特定模型的 tokenizer。

### 2. 最近轮次保留

系统最多保留最近 3 个完整用户轮次，并将工作上下文控制在 12,000 tokens 的软预算内。

一个完整轮次包括用户消息、Assistant 回复、工具调用和工具执行结果。当前轮次始终保留，其他轮次按照从新到旧的顺序加入。对话内容较长时，系统可能只保留最近 1～2 轮。

### 3. 上下文压缩

超出预算的旧轮次由 LLM 压缩为结构化摘要，主要保留：

- 任务目标和用户约束
- 关键决策和执行进度
- 重要文件和其他产物
- 错误信息和测试结果
- 待办事项和未解决问题
- 文件路径、URL、ID 等精确信息

如果当前轮次过长，系统会优先压缩大型工具输出，不直接截断用户的任务要求。

LLM 压缩失败时会重试一次，仍失败则降级为规则摘要。PostgreSQL 中的原始消息不会删除，压缩结果单独保存为上下文快照。

## 记忆机制

Novare 使用一次 LLM 调用同时识别用户画像和情景记忆，并由两个独立的存储服务分别持久化。

### 1. 用户画像

用户画像保存研究方向、回答语言和交互偏好等长期稳定信息：

- 存储在 PostgreSQL
- 默认最多保存 50 条
- 对话前注入系统提示词
- 超出上限时淘汰置信度较低、未锁定的旧记录

### 2. 情景记忆

情景记忆保存研究决策、实验结果、失败经验和任务进度等经历：

- 完整记录存储在 PostgreSQL
- Embedding 向量索引存储在 Milvus
- 根据语义相似度检索，默认最多返回 5 条
- 支持归档和删除，目前尚未实现自动淘汰

### 3. 提取时机

记忆采用批量提取方式：

- 每 4 个完整用户轮次提取一次
- 会话空闲 120 秒后提取
- 切换会话时提取
- 提取或持久化失败时不推进 PostgreSQL 游标，后续可以重试


## TaskState

`TaskState` 用于记录单次用户请求中的临时任务状态，帮助 Agent 在多个工具循环中保持任务方向。

### 1. 保存内容

- 当前任务目标
- 已完成和待办步骤
- 使用过的工具
- 关键发现和缺失信息

### 2. 更新与使用

系统根据用户输入和工具结果，通过规则自动更新 TaskState，不调用额外的 LLM。每次调用主模型前，最新状态会注入 System Prompt。

### 3. 生命周期

TaskState 只在当前 `run_turn` 的多个工具循环中有效，任务结束后立即清空，不写入 PostgreSQL 或 Redis，也不属于长期记忆。


## RAG 混合检索

论文解析完成后，分块正文和元数据会写入 PostgreSQL，同时同步建立 Milvus 向量索引和 Elasticsearch 关键词索引。查询时的默认流程如下：

```text
问题
 ├─ Milvus 向量召回 Top 50
 └─ Elasticsearch BM25 召回 Top 50
              ↓
          RRF 去重融合
              ↓
      取前 20 条候选片段
              ↓
        Qwen3 rerank 重排
              ↓
        返回用户请求的 top_k
```

RRF 使用以下公式融合不同召回通道的排名：

```text
RRF(d) = Σ 1 / (k + rank_i(d))
```

默认 `k=60`。同一个分块被向量检索和 BM25 同时召回时，只保留一条记录并累计两路 RRF 分数。rerank 输出会保留原始的 `vector_rank`、`keyword_rank`、`fusion_score`，并增加 `rerank_rank` 和 `rerank_score`。

### 降级策略

| 故障情况 | 检索行为 |
| --- | --- |
| Elasticsearch 不可用 | 使用 Milvus 向量结果，并返回降级 warning |
| Milvus 不可用 | 使用 Elasticsearch BM25 结果，并返回降级 warning |
| 两路召回都为空 | 在用户有权限的论文范围内尝试 PostgreSQL brute-force 向量检索 |
| Qwen3 rerank 不可用或超时 | 保留 RRF 排序，不中断 RAG 查询 |
| 两路和 fallback 都无结果 | 返回“未找到相关内容” |

检索始终受用户论文权限约束。`paper_id` 或 `paper_ids` 过滤会与用户可访问论文集合取交集，不允许借助过滤参数读取未授权论文。

新解析的论文会自动同步到 Elasticsearch。升级混合检索前已经解析的历史论文需要重新解析或单独执行索引回填，否则 BM25 通道无法召回这些旧分块。

## RAG 回答验证

Novare 可在 RAG 回答发送前启用只读 Verifier，对回答进行二次证据核验：

1. 将回答拆分为最多 12 条原子事实。
2. 在当前用户有权限的论文中对每条事实执行反向 RAG。
3. 判定为 `SUPPORTED`、`CONTRADICTED` 或 `NOT_ENOUGH_EVIDENCE`，并聚合整体风险。
4. 保留受支持内容，修正冲突内容，删除或弱化证据不足的表述。

验证报告会包含事实、判定、风险，以及对应的 `paper_id`、`chunk_id`、章节、原文片段和检索分数。验证失败或超时时返回原始回答，不中断主任务。该功能默认关闭，因为一次回答会增加 2～3 次 LLM 调用，并对每条事实增加一次 RAG 查询。

```env
NOVARE_HALLUCINATION_VERIFIER_ENABLED=true
NOVARE_HALLUCINATION_VERIFIER_MAX_CLAIMS=12
NOVARE_HALLUCINATION_VERIFIER_TOP_K=5
NOVARE_HALLUCINATION_VERIFIER_CONCURRENCY=3
NOVARE_HALLUCINATION_VERIFIER_TIMEOUT=120
```
