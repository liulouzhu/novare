# Novare MVP — Python 编排层设计

## 概述

将 Claw Code 的 Rust 编排层（`ConversationRuntime`）重写为 Python，命名为 **novare**。MVP 目标：单用户 CLI，接入 MiniMax（OpenAI 兼容 API），通过 MCP 协议调用已有的研究工具，内置文件操作工具。

## 架构

```
novare/
  __init__.py
  cli.py                     # CLI 入口（交互式 REPL）
  agent_loop.py              # 核心 agent loop
  llm_client.py              # OpenAI 兼容 API 客户端
  session.py                 # 对话历史 + JSONL 持久化
  tools/
    __init__.py
    file_ops.py              # read_file, write_file, edit_file, glob, grep
    registry.py              # 工具注册表 + 分发
  mcp_client.py              # MCP stdio 客户端
```

## 核心组件

### 1. LLMClient

OpenAI 兼容 API 客户端，负责与 MiniMax 通信。

```python
class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str)
    async def chat(self, messages: list[dict], tools: list[ToolDefinition]) -> LLMResponse
```

- 使用 `httpx.AsyncClient` 调用 `/v1/chat/completions`
- 处理 `tool_use` 响应：解析 `tool_calls` 字段
- 处理 `stop_reason`：`stop` / `tool_calls`
- 返回结构化的 `LLMResponse`（含 content blocks + tool calls）

### 2. Session

对话历史管理 + JSONL 持久化。

```python
class Session:
    session_id: str
    messages: list[dict]       # OpenAI 格式 messages
    workspace_root: Path

    def add_user_message(self, content: str)
    def add_assistant_message(self, content: str, tool_calls: list[dict] = None)
    def add_tool_result(self, tool_call_id: str, content: str)
    def save(self)
    @classmethod
    def load(cls, session_id: str = "latest") -> "Session"
    @classmethod
    def list_sessions(cls) -> list[str]
```

- 存储路径：`workspace/.novare/sessions/<session_id>.jsonl`
- 每条消息一行 JSON（原子写入）
- `latest` 别名解析：找到最近修改的 session 文件

### 3. McpClient

MCP stdio 客户端，连接研究工具 MCP 服务器。

```python
class McpClient:
    def __init__(self, command: str, args: list[str], env: dict = None)
    async def connect(self)
    async def list_tools(self) -> list[ToolDefinition]
    async def call_tool(self, name: str, arguments: dict) -> str
    async def close(self)
```

- 启动子进程（`asyncio.create_subprocess_exec`）
- JSON-RPC over stdin/stdout（`Content-Length` 帧）
- `initialize` → `tools/list` → 懒加载
- `tools/call` 带超时（30s）

### 4. ToolDispatcher

合并内置工具和 MCP 工具，统一执行入口。

```python
class ToolDispatcher:
    def __init__(self, mcp_client: McpClient | None = None)
    async def discover_tools(self) -> list[ToolDefinition]
    async def execute(self, name: str, arguments: dict) -> str
```

- 内置工具表：`read_file`, `write_file`, `edit_file`, `glob_search`, `grep_search`
- MCP 工具：通过 `McpClient.list_tools()` 发现
- `execute()` 路由：内置 → 直接调用；MCP → `McpClient.call_tool()`

### 5. AgentLoop

核心 agent 循环，等价于 Claw Code 的 `ConversationRuntime.run_turn()`。

```python
class AgentLoop:
    def __init__(
        self,
        llm_client: LLMClient,
        tool_dispatcher: ToolDispatcher,
        system_prompt: str,
        max_iterations: int = 20,
    )
    async def run_turn(self, session: Session, user_input: str) -> str
```

**run_turn 流程**：

```
1. session.add_user_message(user_input)
2. loop (max_iterations):
   a. messages = system_prompt + session.messages
   b. response = await llm_client.chat(messages, tools)
   c. assistant_content = extract_content(response)
   d. tool_calls = extract_tool_calls(response)
   e. if no tool_calls:
      - session.add_assistant_message(assistant_content)
      - return assistant_content
   f. session.add_assistant_message(assistant_content, tool_calls)
   g. for each tool_call:
      - result = await tool_dispatcher.execute(name, arguments)
      - session.add_tool_result(tool_call_id, result)
3. return "达到最大迭代次数，请重试。"
```

### 6. CLI 入口

交互式 REPL，读取用户输入，调用 AgentLoop，输出结果。

```python
async def main():
    # 加载配置（环境变量 / .novare/config.json）
    # 初始化 LLMClient, McpClient, ToolDispatcher, Session
    # REPL 循环
    while True:
        user_input = input("novare> ")
        if user_input in ("/exit", "/quit"): break
        if user_input == "/sessions": list_sessions(); continue
        result = await agent_loop.run_turn(session, user_input)
        print(result)
```

## 数据流

```
stdin "帮我找 Transformer 相关论文"
  ↓
AgentLoop.run_turn()
  ↓
LLMClient.chat() → MiniMax API
  ↓
响应: tool_use(paper_search, {query: "Transformer"})
  ↓
ToolDispatcher.execute()
  → McpClient.call_tool("paper_search", {...})
  → research_server.py → Semantic Scholar + arXiv
  ← 返回论文列表
  ↓
结果追加到 session
  ↓
LLMClient.chat() (第二轮，带工具结果)
  ↓
响应: "以下是找到的相关论文..."
  ↓
输出到 stdout
```

## 与 Claw Code 的映射

| Claw Code (Rust) | Novare (Python) | 简化 |
|-------------------|-----------------|------|
| `ConversationRuntime<C, T>` | `AgentLoop` | 去掉泛型，去掉 hook/permission/compaction |
| `ApiClient::stream()` | `LLMClient::chat()` | 同步返回，非流式 |
| `ToolExecutor::execute()` | `ToolDispatcher::execute()` | 内置 + MCP 两种来源 |
| `SessionStore` + `Session` | `Session` | 单用户，不需要 workspace hash |
| `HookRunner` | **跳过** | MVP 不需要 |
| `PermissionPolicy` | **跳过** | 单用户，全部允许 |
| `McpServerManager` | `McpClient` | 只做客户端，不做服务端管理 |
| `UsageTracker` | **跳过** | MVP 不需要 |

## 配置

环境变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `NOVARE_API_KEY` | MiniMax API Key | 必填 |
| `NOVARE_BASE_URL` | API Base URL | `https://api.minimax.chat/v1` |
| `NOVARE_MODEL` | 模型名称 | `MiniMax-Text-01` |
| `NOVARE_DATA_DIR` | 数据目录 | `./data` |
| `NOVARE_WORKSPACE` | 工作空间 | `./workspace` |

MCP 服务器配置（硬编码或从 `.novare/config.json` 读取）：

```json
{
  "mcpServers": {
    "research": {
      "command": ".venv/Scripts/python.exe",
      "args": ["mcp-server/research_server.py"],
      "env": {
        "RESEARCH_DATA_DIR": "./data"
      }
    }
  }
}
```

## 实现顺序

1. `llm_client.py` — OpenAI 兼容 API 客户端 + tool_use 解析
2. `session.py` — 对话历史 + JSONL 持久化
3. `tools/file_ops.py` — 5 个文件操作工具
4. `tools/registry.py` — 工具注册表 + JSON Schema 生成
5. `mcp_client.py` — MCP stdio 客户端
6. `agent_loop.py` — 核心 agent 循环
7. `cli.py` — 交互式 REPL 入口
