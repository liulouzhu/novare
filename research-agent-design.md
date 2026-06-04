---
type: synthesis
tags: [architecture, research-agent, multi-user, system-design]
created: 2026-05-29
updated: 2026-06-04
---

# 智能科研智能体系统设计方案

## 核心论点

基于 Claw Code 框架进行中度改造，构建面向小团队（<50人）的多用户智能科研智能体系统，以文献检索与知识管理为核心场景，通过 Docker 沙箱实现用户隔离，LLM 自主工具调度实现智能协作，并通过 NanoBot 网关层提供 Web + CLI + Telegram 三端接入。

选择 Claw Code 而非 OpenCode 的核心理由：
- **Rust 运行时**：`unsafe_code = "forbid"`，内存安全有编译期保证，适合多用户并发场景
- **成熟的工具注册机制**：`GlobalToolRegistry` 已支持内置工具 + 插件工具 + 运行时工具的组合式注册，天然适合动态工具扩展
- **MCP 协议原生支持**：完整客户端/服务器 MCP 实现，工具可以通过独立进程暴露，天然支持沙箱隔离
- **Hook 系统**：PreToolUse/PostToolUse/PostToolUseFailure 三阶段钩子，可用于注入用户上下文和权限控制
- **多提供商 API 客户端**：`ProviderClient` 枚举已支持 Anthropic/OpenAI/xAI，多模型路由只需扩展枚举

## 系统架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    接入层 (NanoBot Gateway)                │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │ Web Chat │  │ CLI/TUI  │  │ Telegram Bot         │  │
│  └────┬─────┘  └────┬─────┘  └──────────┬───────────┘  │
│       └──────────────┼───────────────────┘              │
│                      ▼                                  │
│            NanoBot 网关 (协议适配 + 消息路由)              │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  编排层 (Claw Code Runtime 改造)           │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ SessionStore │  │ ProviderClient│  │ GlobalTool    │  │
│  │ (多用户隔离)  │  │ (多模型路由)  │  │ Registry      │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Conversation │  │ HookRunner   │  │ Permission    │  │
│  │ Runtime      │  │ (钩子系统)   │  │ Policy        │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  沙箱层 (Docker 容器隔离)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ 用户 A   │  │ 用户 B   │  │ 用户 C   │  ...         │
│  │ 容器     │  │ 容器     │  │ 容器     │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  每个容器内运行 MCP Server（stdio 传输）                  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  工具层 (Research Tools)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ 论文检索  │  │ 文献解析  │  │ 知识图谱  │              │
│  │ 工具     │  │ 工具     │  │ 工具     │              │
│  ├──────────┤  ├──────────┤  ├──────────┤              │
│  │ RAG 查询  │  │ 数据分析  │  │ 代码执行  │              │
│  │ 工具     │  │ 工具     │  │ 工具     │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  通过 MCP 协议注册到 GlobalToolRegistry                   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  存储层                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Qdrant       │  │ Neo4j        │  │ PostgreSQL   │   │
│  │ (向量检索)    │  │ (知识图谱)    │  │ (用户/元数据) │   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │ claw-rag-service (工作区 RAG，已内建)              │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## 模块详细设计

### 1. NanoBot 网关层（接入层）

**职责**：统一协议适配，将不同渠道的消息转为标准化的内部消息格式。

| 组件 | 说明 |
|------|------|
| **协议适配器** | 每个渠道一个适配器（Web/Telegram/CLI），负责消息格式转换 |
| **消息路由器** | 根据用户身份和会话 ID，路由到对应的编排层实例 |
| **连接管理** | WebSocket（Web）、Long Polling（Telegram）、stdin/stdout（CLI） |
| **认证中间件** | 从各渠道提取用户身份，验证后注入上下文 |

**NanoBot 魔改要点**：
- 新增统一消息协议：`{ userId, sessionId, channel, content, metadata }`
- Telegram 适配器支持 Markdown 渲染、文件收发（论文 PDF）
- Web 端提供 SSE/WebSocket 的实时流式输出
- CLI 适配器复用 Claw Code 原有的 `LiveCli` TUI 渲染（`crossterm` + `syntect`），通过 socket/pipe 与网关通信

### 2. Claw Code Runtime 改造（编排层）

**改造策略**：Claw Code 的 Rust crate 结构天然支持模块化改造，核心改动集中在 `runtime` crate。

**魔改范围（中度修改）**：

| 改造点 | 原始行为（Claw Code） | 改造后行为 |
|--------|----------------------|-----------|
| **会话管理** | `SessionStore` 单用户、按 workspace hash 隔离 | 多用户并发会话，SessionStore 扩展为 `MultiUserSessionStore`，按 `(userId, workspace)` 隔离 |
| **工具注册** | `GlobalToolRegistry` 组合式注册 | 保留原机制，新增 MCP 工具动态注册（每个用户沙箱通过 stdio MCP 暴露研究工具） |
| **模型调用** | `ProviderClient` 枚举（Anthropic/OpenAI/xAI） | 扩展为多模型路由器，按任务类型分发到不同 ProviderClient |
| **用户上下文** | 无 | 通过 `HookRunner` PreToolUse 钩子注入 `userId` + `sandboxId` |
| **插件接口** | `PluginManifest` + `PluginRegistry` | 研究工具以 MCP Server 形式接入，利用已有的 `McpServerManager` |
| **权限系统** | `PermissionPolicy` 5 种模式 | 扩展为多用户 RBAC，每个用户绑定独立的 PermissionPolicy |

**多模型路由器设计**：

```rust
// 扩展 ProviderClient 枚举
enum ResearchRouter {
    // 长上下文任务 → Claude
    LongContext(AnthropicClient),
    // 代码任务 → Claude / GPT-4
    Code(AnthropicClient),
    // 简单任务 → 轻量模型
    Lightweight(OpenAiCompatClient),
    // 工具路由 → 不经过 LLM
    DirectTool,
}

impl ResearchRouter {
    fn route(task_type: &TaskType) -> Self {
        match task_type {
            TaskType::LiteratureReview => Self::LongContext(/* Claude */),
            TaskType::CodeGeneration => Self::Code(/* Claude/GPT-4 */),
            TaskType::SimpleQA => Self::Lightweight(/* Haiku/GPT-4o-mini */),
            TaskType::KnowledgeGraphQuery => Self::DirectTool,
        }
    }
}
```

路由策略：默认 LLM 自主选择，可通过 `.claw.json` 配置覆盖特定任务的模型。

**Claw Code 原生能力的直接复用**：

| 原生能力 | 复用方式 |
|---------|---------|
| `ConversationRuntime<C, T>` | 泛型参数化，替换 `T` 为支持沙箱的 `SandboxToolExecutor` |
| `HookRunner` | PreToolUse 钩子注入用户上下文；PostToolUse 钩子触发知识入库（向量化 + 实体抽取） |
| `PermissionPolicy` | 每用户独立策略，沙箱外操作需审批，沙箱内操作自动放行 |
| `McpServerManager` | 每个用户沙箱容器运行一个 MCP Server（stdio），管理器负责生命周期 |
| `SessionCompaction` | 利用已有的确定性压缩机制，研究对话通常很长 |
| `SessionStore` | JSONL 持久化 + 自动脱敏 + 字段截断，适合存储研究对话历史 |
| `claw-rag-service` | 直接复用为工作区 RAG 服务（900 字符分块、text-embedding-3-small、SQLite 存储） |

### 3. Docker 沙箱层（用户隔离）

**设计原则**：每个用户的代码执行和文件操作都在独立容器中进行。容器通过 MCP stdio 协议与 Claw Code Runtime 通信。

```
┌─ 用户沙箱容器 ──────────────────────────────────┐
│  /workspace/              # 用户工作空间         │
│  /workspace/papers/       # 下载的论文           │
│  /workspace/data/         # 数据文件             │
│  /workspace/code/         # 用户代码             │
│  ──────────────────────────────────────────────  │
│  预装：Python, Node.js, LaTeX, pandoc           │
│  ──────────────────────────────────────────────  │
│  MCP Server (stdio)                              │
│  ├── paper_search 工具                           │
│  ├── paper_parse 工具                            │
│  ├── rag_query 工具                              │
│  ├── knowledge_graph 工具                        │
│  ├── code_execute 工具                           │
│  └── file_ops 工具                               │
│  ──────────────────────────────────────────────  │
│  网络：受限出口（仅允许学术资源域名）               │
│  资源：CPU/MEM 限制（cgroup）                     │
└──────────────────────────────────────────────────┘
```

**与 Claw Code 的对接方式**：

Claw Code 的 `McpServerManager` 已支持 stdio 传输的 MCP 服务器。改造方式：

1. 每个用户沙箱容器启动时运行一个 MCP Server 进程（Python/Node.js）
2. 研究工具通过 MCP 协议以 JSON Schema 暴露工具描述
3. `McpServerManager` 通过 Docker exec 或 docker-py 启动容器内的 MCP 进程
4. 工具调用通过 stdin/stdout 传输，天然实现了进程级隔离

```toml
# .claw.json 配置示例
[mcpServers.research-sandbox]
command = "docker"
args = ["exec", "-i", "sandbox-{userId}", "python", "mcp_server.py"]
```

**沙箱管理器**：
- **容器池**：预启动 N 个容器，用户接入时分配，减少冷启动
- **生命周期**：空闲 30 分钟暂停，2 小时无活动销毁
- **文件持久化**：用户工作空间挂载到宿主机 volume，容器销毁不丢数据
- **快照恢复**：暂停的容器可快速恢复到之前状态

**资源限制**：
- 每用户：2 CPU / 2GB RAM / 10GB 磁盘
- 网络白名单：仅允许 arXiv, PubMed, Semantic Scholar 等学术域名
- 执行超时：单次命令 5 分钟（利用 Claw Code 的 bash 执行超时机制）

### 4. 工具层（Research Tools）

研究工具以 MCP Server 形式运行在用户沙箱容器内，通过标准 MCP 协议注册到 Claw Code 的 `GlobalToolRegistry`。LLM 通过工具描述自主判断需要调用哪个工具。

#### 4.1 论文检索工具
```json
{
  "name": "paper_search",
  "description": "从 arXiv/PubMed/Semantic Scholar 检索学术论文",
  "parameters": {
    "query": "搜索关键词或自然语言描述",
    "source": "arxiv | pubmed | semantic_scholar",
    "limit": "返回数量 (默认 10)",
    "year_range": "年份范围 [2020, 2026]"
  },
  "returns": "论文列表（标题、作者、摘要、DOI、PDF链接）"
}
```

#### 4.2 文献解析工具
```json
{
  "name": "paper_parse",
  "description": "解析论文 PDF，提取结构化内容（摘要、方法、结论、引用）",
  "parameters": {
    "source": "PDF 文件路径或 DOI",
    "sections": "要提取的章节 [abstract, methods, results, references]"
  },
  "returns": "结构化论文内容"
}
```

#### 4.3 RAG 查询工具
```json
{
  "name": "rag_query",
  "description": "在已摄入的知识库中进行语义检索和问答",
  "parameters": {
    "question": "自然语言问题",
    "scope": "检索范围 (个人库 | 团队库 | 全部)",
    "top_k": "返回相关片段数量"
  },
  "returns": "相关文档片段 + 引用来源"
}
```

#### 4.4 知识图谱工具
```json
{
  "name": "knowledge_graph",
  "description": "查询或更新知识图谱（概念、实体、关系）",
  "parameters": {
    "action": "query | add_entity | add_relation | find_path",
    "subject": "主体实体",
    "predicate": "关系类型",
    "object": "客体实体"
  },
  "returns": "图谱查询结果或操作确认"
}
```

#### 4.5 代码执行工具（沙箱内执行）
```json
{
  "name": "code_execute",
  "description": "在用户沙箱中执行 Python/Shell 代码",
  "parameters": {
    "language": "python | shell",
    "code": "要执行的代码",
    "timeout": "超时秒数"
  },
  "returns": "stdout + stderr + 执行状态"
}
```

#### 4.6 文件操作工具
Claw Code 内置的 `read_file`、`write_file`、`edit_file`、`glob_search`、`grep_search` 工具可直接复用，只需通过 `PermissionPolicy` 将文件操作限制在用户工作空间内。

### 5. 存储层

| 组件 | 用途 | 数据模型 |
|------|------|---------|
| **Qdrant** | 论文向量、文档片段向量、RAG 检索 | 集合按用户/团队分 namespace |
| **Neo4j** | 知识图谱（概念 → 关系 → 实体） | 节点：论文、概念、作者、机构；边：引用、相关、作者 |
| **PostgreSQL** | 用户账号、会话记录、论文元数据、操作日志 | 标准关系表 |
| **claw-rag-service** | 工作区级 RAG（已内建） | SQLite + blake3 内容哈希，900 字符分块，cosine similarity |
| **JSONL 会话文件** | 对话历史持久化（Claw Code 内建） | `.claw/sessions/<workspace_hash>/`，自动脱敏 + 字段截断 |

**数据流向**：
```
论文 PDF → 解析工具(MCP) → 结构化文本 → 向量化 → Qdrant
                                      → 实体抽取 → Neo4j
                                      → 元数据 → PostgreSQL
                                      → 工作区索引 → claw-rag-service
```

### 6. 知识自动入库（PostToolUse 钩子）

Claw Code 的 `HookRunner` 提供了 `PostToolUse` 钩子，可在工具执行完成后触发自定义逻辑。利用这一机制实现**研究知识自动入库**：

```bash
# .claw.json 配置
{
  "hooks": {
    "postToolUse": [
      "python scripts/knowledge_ingest.py --tool $TOOL_NAME --output $TOOL_OUTPUT --user $USER_ID"
    ]
  }
}
```

入库流程：
1. `paper_search` 执行后 → 论文元数据写入 PostgreSQL + 向量化写入 Qdrant
2. `paper_parse` 执行后 → 结构化内容向量化 + 实体抽取写入 Neo4j
3. `rag_query` 执行后 → 查询结果记录到审计日志
4. `code_execute` 执行后 → 执行结果追加到会话历史

### 7. 用户认证与权限

**Phase 1（初期）**：
- 简单的邮箱 + 密码注册/登录
- JWT Token 认证
- 每个用户绑定一个沙箱实例 + 独立的 `PermissionPolicy`
- 团队空间：创建者可邀请成员共享知识库

**Phase 2（后续）**：
- 接入机构 SSO/OAuth 2.0（Claw Code 已有 `oauth.rs` PKCE 流程实现）
- RBAC 角色权限（管理员 / 普通用户 / 访客）
- 审计日志（利用 Claw Code 的 `SessionTracer` telemetry 系统）

### 8. 部署方案

**Phase 1：Docker Compose**

```yaml
# docker-compose.yml 核心服务
services:
  gateway:        # NanoBot 网关 (Web + API)
  core:           # Claw Code Runtime 改造后的编排服务 (Rust 二进制)
  sandbox-pool:   # 沙箱容器管理器
  rag-service:    # claw-rag-service (Axum HTTP, 端口 8787)
  qdrant:         # 向量数据库
  neo4j:          # 图数据库
  postgres:       # 关系数据库
  redis:          # 会话缓存 + 消息队列
  nginx:          # 反向代理 + TLS
```

**Phase 2：Kubernetes**
- 网关层 → Deployment + Ingress
- 沙箱 → Pod per user（更容易弹性伸缩）
- 存储 → StatefulSet + PVC
- HPA 自动扩缩容

## 关键数据流

### 用户提问到回答的完整链路

```
1. 用户通过 Telegram 发送："帮我找最近两年关于 Transformer 在分子生成中的应用"
     │
2. NanoBot 网关（Telegram 适配器）
     │ → 解析消息 → 识别用户身份 → 包装为统一消息协议
     ▼
3. 编排层（Claw Code Runtime）
     │ → MultiUserSessionStore 创建/恢复用户会话
     │ → ResearchRouter：选择 Claude（需要长上下文理解）
     │ → ConversationRuntime.run_turn() 进入 Agent Loop
     ▼
4. LLM 分析任务 → 请求调用 paper_search 工具
     │
5. HookRunner.run_pre_tool_use_hook()
     │ → 注入 userId + sandboxId 到工具上下文
     ▼
6. PermissionPolicy.authorize()
     │ → 检查用户权限 → Allow
     ▼
7. McpServerManager → stdin/stdout → 用户沙箱 MCP Server
     │ → 调用 Semantic Scholar API 检索论文
     │ → 返回 10 篇相关论文列表
     ▼
8. HookRunner.run_post_tool_use_hook()
     │ → 触发 knowledge_ingest.py 自动入库
     ▼
9. LLM 继续决策
     │ → 解析结果，发现用户可能需要详细内容
     │ → 请求调用 paper_parse 工具（并行解析前 3 篇 PDF，串行执行）
     ▼
10. 工具执行
     │ → 沙箱内下载 PDF → 解析提取结构化内容
     │ → PostToolUse 钩子：向量化存入 Qdrant + 实体抽取存入 Neo4j
     ▼
11. LLM 生成综述回答
     │ → 引用具体论文和页码
     │ → 流式输出（SSE 事件流）
     ▼
12. NanoBot 网关（Telegram 适配器）
     │ → Markdown 格式化 → 分段发送（Telegram 消息长度限制）
     ▼
13. 用户收到回答（附论文链接和引用）
```

## 技术栈总结

| 层 | 技术选型 |
|----|---------|
| 接入层 | NanoBot (Go) + Telegram Bot API + WebSocket |
| 编排层 | Claw Code Runtime 改造 (**Rust**, Tokio) + 自研路由器 |
| 沙箱层 | Docker + Docker API（容器内 MCP Server） |
| 工具层 | MCP 协议（stdio 传输）+ Python/Node.js 工具脚本 |
| 内建 RAG | claw-rag-service（Axum HTTP, SQLite, text-embedding-3-small） |
| 向量库 | Qdrant |
| 图数据库 | Neo4j |
| 关系数据库 | PostgreSQL |
| 会话持久化 | JSONL（Claw Code 内建，自动脱敏 + 字段截断 + 文件轮转） |
| 缓存/队列 | Redis |
| 部署 | Docker Compose → Kubernetes |
| LLM | Claude (主力) + GPT-4o + 轻量模型（通过 ProviderClient 枚举） |

## Claw Code 改造映射表

| Claw Code 模块 | 改造方式 | 改造难度 |
|----------------|---------|---------|
| `runtime/session.rs` | 扩展 SessionStore 支持多用户隔离 | 低 |
| `runtime/conversation.rs` | 无需修改，泛型 `ConversationRuntime<C, T>` 天然支持替换 | 无 |
| `runtime/config.rs` | 新增研究工具相关配置字段 | 低 |
| `runtime/hooks.rs` | 配置 PostToolUse 钩子脚本（无需改代码） | 无 |
| `runtime/permissions.rs` | 扩展为多用户 RBAC | 中 |
| `runtime/mcp_server.rs` | 适配 Docker 容器内 MCP Server 的生命周期管理 | 中 |
| `runtime/prompt.rs` | 新增研究场景系统提示词模板 | 低 |
| `api/client.rs` | 扩展 ProviderClient 支持更多模型 | 低 |
| `tools/lib.rs` | 注册研究领域自定义工具 | 低 |
| `claw-rag-service/` | 直接复用，配置接入主循环 | 低 |

## 开发路线建议

### Phase 1：MVP（4-6 周）
- Claw Code 基础改造：多用户会话（MultiUserSessionStore）+ MCP 沙箱对接
- Docker 沙箱管理器（单机）+ 容器内 MCP Server
- 核心工具：paper_search + rag_query + code_execute
- NanoBot 网关：Web + CLI 接入
- PostgreSQL + Qdrant + claw-rag-service 存储

### Phase 2：知识管理增强（3-4 周）
- paper_parse 工具完善（PDF 全文解析，集成 Nougat/Grobid）
- 知识图谱工具（Neo4j 集成）
- ResearchRouter 多模型路由器
- PostToolUse 钩子实现知识自动入库
- Telegram Bot 接入

### Phase 3：团队协作（2-3 周）
- 团队共享知识库
- 协作标注
- 简单认证系统（JWT + Claw Code OAuth 扩展）
- SessionTracer 审计日志

### Phase 4：生产化（2-3 周）
- K8s 部署
- SSO 接入（利用 Claw Code 已有的 OAuth PKCE 实现）
- 监控告警
- 性能优化（Rust 运行时天然高性能）

## 风险与注意事项

- **Rust 改造成本**：Claw Code 是 Rust 项目，改造需要 Rust 工程能力。但核心改造集中在配置和扩展已有接口，无需深入修改 `ConversationRuntime` 等核心逻辑
- **MCP 沙箱延迟**：通过 Docker exec 调用容器内 MCP Server 存在网络开销，建议使用 Docker API 直接 exec（而非 CLI），并复用长连接
- **PDF 解析质量**：学术论文的 PDF 解析（尤其是数学公式、表格、图表）仍然是技术难点，建议集成 Nougat 或 Grobid 等专用工具
- **NanoBot 适配**：需确认 NanoBot 的现有架构是否支持自定义适配器扩展，可能需要 fork 后修改
- **知识图谱维护**：自动构建的知识图谱可能包含噪声，需要定期的人工审核或置信度过滤机制
- **会话文件膨胀**：研究对话通常很长，Claw Code 的确定性压缩（规则提取）可能丢失重要研究上下文，建议在研究场景中提高压缩阈值或结合 claw-rag-service 做长期检索

## 来源
- [[claw-code]] - Rust 实现的开源 CLI AI 编码助手框架
- [[NanoBot]] - 网关层框架
- 用户需求讨论 - 2026-05-29
- Claw Code 源码分析 - 2026-06-04
