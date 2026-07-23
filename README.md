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
