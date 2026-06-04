# Novare MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite Claw Code's orchestration layer in Python as `novare`, enabling an agent loop with LLM (MiniMax) + MCP research tools + file operations.

**Architecture:** A CLI REPL that feeds user input to an agent loop. The loop calls MiniMax via OpenAI-compatible API, dispatches tool calls to either built-in file operations or an MCP server (existing research tools), and iterates until the model produces a final answer.

**Tech Stack:** Python 3.10+, httpx, mcp (client), pytest

---

## File Structure

```
novare/
  __init__.py              # Package marker
  cli.py                   # CLI REPL entry point
  agent_loop.py            # Core agent loop (ConversationRuntime equivalent)
  llm_client.py            # OpenAI-compatible API client
  session.py               # Conversation history + JSONL persistence
  config.py                # Configuration loading (env vars + config file)
  tools/
    __init__.py
    file_ops.py            # 5 file operation tools
    registry.py            # Tool registry + dispatch + JSON Schema generation
  mcp_client.py            # MCP stdio client
tests/
  __init__.py
  test_llm_client.py
  test_session.py
  test_file_ops.py
  test_registry.py
  test_mcp_client.py
  test_agent_loop.py
  conftest.py              # Shared fixtures
novare_test_harness.py     # Manual integration test script
pyproject.toml             # novare package config (separate from mcp-server)
```

---

## Task 1: Project Skeleton + Config

**Files:**
- Create: `novare/__init__.py`
- Create: `novare/config.py`
- Create: `novare/tools/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create package directories and __init__.py files**

```bash
mkdir -p novare/tools tests
touch novare/__init__.py novare/tools/__init__.py tests/__init__.py
```

- [ ] **Step 2: Create config.py**

```python
"""Novare 配置加载 — 环境变量 + .novare/config.json"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class McpServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class NovareConfig:
    api_key: str = ""
    base_url: str = "https://api.minimax.chat/v1"
    model: str = "MiniMax-Text-01"
    data_dir: Path = Path("./data")
    workspace: Path = Path("./workspace")
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    system_prompt: str = ""

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "NovareConfig":
        """从环境变量和配置文件加载配置"""
        cfg = cls()

        # 环境变量覆盖
        cfg.api_key = os.environ.get("NOVARE_API_KEY", cfg.api_key)
        cfg.base_url = os.environ.get("NOVARE_BASE_URL", cfg.base_url)
        cfg.model = os.environ.get("NOVARE_MODEL", cfg.model)

        data_dir = os.environ.get("NOVARE_DATA_DIR")
        if data_dir:
            cfg.data_dir = Path(data_dir)

        workspace = os.environ.get("NOVARE_WORKSPACE")
        if workspace:
            cfg.workspace = Path(workspace)

        # 配置文件
        path = Path(config_path) if config_path else cfg.workspace / ".novare" / "config.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "mcpServers" in data:
                for name, srv in data["mcpServers"].items():
                    cfg.mcp_servers[name] = McpServerConfig(
                        command=srv["command"],
                        args=srv.get("args", []),
                        env=srv.get("env", {}),
                    )

        # 默认研究工具 MCP 服务器
        if not cfg.mcp_servers and (cfg.workspace / "mcp-server").exists():
            venv_python = cfg.workspace / "mcp-server" / ".venv" / "Scripts" / "python.exe"
            if not venv_python.exists():
                venv_python = cfg.workspace / "mcp-server" / ".venv" / "bin" / "python"
            mcp_server_py = cfg.workspace / "mcp-server" / "research_server.py"
            if venv_python.exists() and mcp_server_py.exists():
                cfg.mcp_servers["research"] = McpServerConfig(
                    command=str(venv_python),
                    args=[str(mcp_server_py)],
                    env={"RESEARCH_DATA_DIR": str(cfg.data_dir)},
                )

        # 默认系统提示词
        if not cfg.system_prompt:
            cfg.system_prompt = _default_system_prompt(cfg.workspace)

        return cfg


def _default_system_prompt(workspace: Path) -> str:
    return f"""你是 Novare 智能科研助手。

你的能力：
- 搜索学术论文（paper_search）
- 解析论文 PDF（paper_parse）
- 在已解析论文中语义检索（rag_query）
- 查询/更新论文知识图谱（knowledge_graph）
- 执行 Python 代码做数据分析（code_execute）
- 读写文件（read_file, write_file, edit_file）
- 搜索文件（glob_search, grep_search）

工作空间：{workspace}

工作流指引：
1. 先搜索获取论文列表和 ID
2. 用 paper_parse 解析感兴趣的论文 PDF
3. 用 rag_query 在已解析的论文库中语义问答
4. 用 knowledge_graph 构建概念关系图谱
5. 用 code_execute 进行数据分析和可视化

输出规范：
- 引用论文时提供标题、作者、年份
- 综述回答按主题组织，引用具体论文
- 区分已解析论文（有全文）和仅检索到的论文（仅有摘要）
- 使用中文与用户交互，搜索词建议使用英文以获得更好的检索效果
"""
```

- [ ] **Step 3: Create tests/conftest.py**

```python
"""共享测试 fixtures"""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path):
    """创建临时工作空间"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".novare").mkdir()
    return ws


@pytest.fixture
def tmp_data_dir(tmp_path):
    """创建临时数据目录"""
    d = tmp_path / "data"
    d.mkdir()
    return d
```

- [ ] **Step 4: Commit**

```bash
git add novare/ tests/
git commit -m "feat: novare project skeleton + config"
```

---

## Task 2: LLM Client

**Files:**
- Create: `novare/llm_client.py`
- Create: `tests/test_llm_client.py`

- [ ] **Step 1: Write failing test**

```python
"""tests/test_llm_client.py"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from novare.llm_client import LLMClient, LLMResponse, ToolCall


class TestLLMResponse:
    def test_text_response(self):
        resp = LLMResponse(
            content="Hello world",
            tool_calls=[],
            stop_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert resp.content == "Hello world"
        assert resp.tool_calls == []
        assert resp.stop_reason == "stop"

    def test_tool_call_response(self):
        tc = ToolCall(id="call_1", name="paper_search", arguments={"query": "test"})
        resp = LLMResponse(
            content="",
            tool_calls=[tc],
            stop_reason="tool_calls",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
        )
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "paper_search"
        assert resp.tool_calls[0].arguments == {"query": "test"}


class TestParseResponse:
    def test_parse_text_only(self):
        from novare.llm_client import parse_chat_response
        raw = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hello"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        resp = parse_chat_response(raw)
        assert resp.content == "Hello"
        assert resp.tool_calls == []
        assert resp.stop_reason == "stop"

    def test_parse_tool_calls(self):
        from novare.llm_client import parse_chat_response
        raw = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "paper_search",
                            "arguments": json.dumps({"query": "test"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        resp = parse_chat_response(raw)
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].id == "call_abc"
        assert resp.tool_calls[0].name == "paper_search"
        assert resp.stop_reason == "tool_calls"

    def test_parse_empty_content_with_tool_calls(self):
        from novare.llm_client import parse_chat_response
        raw = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "/tmp/test.txt"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        }
        resp = parse_chat_response(raw)
        assert resp.content == ""
        assert len(resp.tool_calls) == 1


class TestLLMClient:
    @pytest.mark.asyncio
    async def test_chat_makes_http_request(self):
        client = LLMClient(api_key="test-key", base_url="https://api.test.com/v1", model="test-model")
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hi there"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            resp = await client.chat([{"role": "user", "content": "Hello"}])
            assert resp.content == "Hi there"
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "/chat/completions" in call_args[0][0]
            body = call_args[1]["json"]
            assert body["model"] == "test-model"
            assert body["messages"][0]["content"] == "Hello"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_llm_client.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'novare.llm_client'`

- [ ] **Step 3: Write implementation**

```python
"""novare/llm_client.py — OpenAI 兼容 API 客户端"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("novare.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict = field(default_factory=dict)


def parse_chat_response(raw: dict) -> LLMResponse:
    """解析 OpenAI 兼容的 chat completion 响应"""
    choice = raw["choices"][0]
    message = choice["message"]
    stop_reason = choice.get("finish_reason", "stop")
    content = message.get("content") or ""
    tool_calls = []

    for tc in message.get("tool_calls", []):
        func = tc["function"]
        tool_calls.append(ToolCall(
            id=tc["id"],
            name=func["name"],
            arguments=json.loads(func["arguments"]) if isinstance(func["arguments"], str) else func["arguments"],
        ))

    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=raw.get("usage", {}),
    )


class LLMClient:
    """OpenAI 兼容 API 客户端（接 MiniMax）"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """调用 chat completion API"""
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        logger.debug("LLM request: model=%s, messages=%d, tools=%d",
                      self.model, len(messages), len(tools or []))

        resp = await self._http.post("/chat/completions", json=body)
        resp.raise_for_status()
        raw = resp.json()

        result = parse_chat_response(raw)
        logger.debug("LLM response: stop_reason=%s, tool_calls=%d, content_len=%d",
                      result.stop_reason, len(result.tool_calls), len(result.content))
        return result

    async def close(self):
        await self._http.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_llm_client.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add novare/llm_client.py tests/test_llm_client.py
git commit -m "feat: llm_client — OpenAI-compatible API client with tool_use parsing"
```

---

## Task 3: Session

**Files:**
- Create: `novare/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing test**

```python
"""tests/test_session.py"""

import json
from pathlib import Path

import pytest

from novare.session import Session


class TestSession:
    def test_create_session(self, tmp_workspace):
        session = Session(workspace=tmp_workspace)
        assert session.session_id is not None
        assert session.messages == []

    def test_add_user_message(self, tmp_workspace):
        session = Session(workspace=tmp_workspace)
        session.add_user_message("Hello")
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "Hello"

    def test_add_assistant_message(self, tmp_workspace):
        session = Session(workspace=tmp_workspace)
        session.add_assistant_message("Hi there")
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "assistant"
        assert session.messages[0]["content"] == "Hi there"

    def test_add_assistant_message_with_tool_calls(self, tmp_workspace):
        session = Session(workspace=tmp_workspace)
        tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "paper_search", "arguments": "{}"}}]
        session.add_assistant_message("", tool_calls=tool_calls)
        msg = session.messages[0]
        assert msg["tool_calls"] == tool_calls

    def test_add_tool_result(self, tmp_workspace):
        session = Session(workspace=tmp_workspace)
        session.add_tool_result("call_1", "result text")
        msg = session.messages[0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_1"
        assert msg["content"] == "result text"

    def test_save_and_load(self, tmp_workspace):
        session = Session(workspace=tmp_workspace)
        session.add_user_message("Test message")
        session.save()

        loaded = Session.load(session.session_id, workspace=tmp_workspace)
        assert loaded.session_id == session.session_id
        assert len(loaded.messages) == 1
        assert loaded.messages[0]["content"] == "Test message"

    def test_load_latest(self, tmp_workspace):
        s1 = Session(workspace=tmp_workspace)
        s1.add_user_message("First")
        s1.save()

        s2 = Session(workspace=tmp_workspace)
        s2.add_user_message("Second")
        s2.save()

        loaded = Session.load("latest", workspace=tmp_workspace)
        assert loaded.session_id == s2.session_id

    def test_list_sessions(self, tmp_workspace):
        s1 = Session(workspace=tmp_workspace)
        s1.add_user_message("A")
        s1.save()
        s2 = Session(workspace=tmp_workspace)
        s2.add_user_message("B")
        s2.save()

        sessions = Session.list_sessions(workspace=tmp_workspace)
        assert len(sessions) == 2
        assert s1.session_id in sessions
        assert s2.session_id in sessions

    def test_delete_session(self, tmp_workspace):
        session = Session(workspace=tmp_workspace)
        session.add_user_message("Delete me")
        session.save()
        sid = session.session_id

        session.delete()
        assert not (tmp_workspace / ".novare" / "sessions" / f"{sid}.jsonl").exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_session.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'novare.session'`

- [ ] **Step 3: Write implementation**

```python
"""novare/session.py — 对话历史 + JSONL 持久化"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class Session:
    def __init__(self, session_id: str | None = None, workspace: Path = Path(".")):
        self.session_id = session_id or self._generate_id()
        self.workspace = workspace
        self.messages: list[dict] = []
        self._dir = workspace / ".novare" / "sessions"

    @staticmethod
    def _generate_id() -> str:
        ts = int(time.time())
        short = uuid.uuid4().hex[:8]
        return f"{ts}-{short}"

    @property
    def _path(self) -> Path:
        return self._dir / f"{self.session_id}.jsonl"

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str, tool_calls: list[dict] | None = None):
        msg: dict = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, content: str):
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def save(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            for msg in self.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, session_id: str = "latest", workspace: Path = Path(".")) -> Session:
        sessions_dir = workspace / ".novare" / "sessions"
        if session_id == "latest":
            files = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            if not files:
                raise FileNotFoundError("No sessions found")
            target = files[-1]
            sid = target.stem
        else:
            sid = session_id
            target = sessions_dir / f"{sid}.jsonl"

        session = cls(session_id=sid, workspace=workspace)
        with open(target, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    session.messages.append(json.loads(line))
        return session

    @classmethod
    def list_sessions(cls, workspace: Path = Path(".")) -> list[str]:
        sessions_dir = workspace / ".novare" / "sessions"
        if not sessions_dir.exists():
            return []
        return [p.stem for p in sorted(sessions_dir.glob("*.jsonl"))]

    def delete(self):
        if self._path.exists():
            self._path.unlink()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_session.py -v
```

Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add novare/session.py tests/test_session.py
git commit -m "feat: session — conversation history with JSONL persistence"
```

---

## Task 4: File Operation Tools

**Files:**
- Create: `novare/tools/file_ops.py`
- Create: `tests/test_file_ops.py`

- [ ] **Step 1: Write failing test**

```python
"""tests/test_file_ops.py"""

import pytest

from novare.tools.file_ops import (
    handle_read_file,
    handle_write_file,
    handle_edit_file,
    handle_glob_search,
    handle_grep_search,
)


class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_workspace):
        (tmp_workspace / "test.txt").write_text("Hello world", encoding="utf-8")
        result = await handle_read_file({"path": str(tmp_workspace / "test.txt")}, workspace=tmp_workspace)
        assert "Hello world" in result

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tmp_workspace):
        result = await handle_read_file({"path": str(tmp_workspace / "nope.txt")}, workspace=tmp_workspace)
        assert "Error" in result or "error" in result.lower()


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_workspace):
        path = tmp_workspace / "new.txt"
        result = await handle_write_file({"path": str(path), "content": "test data"}, workspace=tmp_workspace)
        assert "OK" in result or "written" in result.lower() or "success" in result.lower()
        assert path.read_text(encoding="utf-8") == "test data"

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, tmp_workspace):
        path = tmp_workspace / "existing.txt"
        path.write_text("old", encoding="utf-8")
        await handle_write_file({"path": str(path), "content": "new"}, workspace=tmp_workspace)
        assert path.read_text(encoding="utf-8") == "new"


class TestEditFile:
    @pytest.mark.asyncio
    async def test_edit_replaces_string(self, tmp_workspace):
        path = tmp_workspace / "edit.txt"
        path.write_text("Hello world", encoding="utf-8")
        result = await handle_edit_file({
            "path": str(path),
            "old_string": "world",
            "new_string": "Python",
        }, workspace=tmp_workspace)
        assert "OK" in result or "success" in result.lower() or "replaced" in result.lower()
        assert path.read_text(encoding="utf-8") == "Hello Python"

    @pytest.mark.asyncio
    async def test_edit_string_not_found(self, tmp_workspace):
        path = tmp_workspace / "edit.txt"
        path.write_text("Hello world", encoding="utf-8")
        result = await handle_edit_file({
            "path": str(path),
            "old_string": "xyz",
            "new_string": "abc",
        }, workspace=tmp_workspace)
        assert "Error" in result or "not found" in result.lower() or "not match" in result.lower()


class TestGlobSearch:
    @pytest.mark.asyncio
    async def test_glob_finds_files(self, tmp_workspace):
        (tmp_workspace / "a.py").write_text("x=1", encoding="utf-8")
        (tmp_workspace / "b.py").write_text("y=2", encoding="utf-8")
        (tmp_workspace / "c.txt").write_text("z=3", encoding="utf-8")
        result = await handle_glob_search({"pattern": "*.py", "path": str(tmp_workspace)}, workspace=tmp_workspace)
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result


class TestGrepSearch:
    @pytest.mark.asyncio
    async def test_grep_finds_content(self, tmp_workspace):
        (tmp_workspace / "f1.py").write_text("def hello():\n    pass", encoding="utf-8")
        (tmp_workspace / "f2.py").write_text("def world():\n    pass", encoding="utf-8")
        result = await handle_grep_search({"pattern": "hello", "path": str(tmp_workspace)}, workspace=tmp_workspace)
        assert "hello" in result.lower()

    @pytest.mark.asyncio
    async def test_grep_no_match(self, tmp_workspace):
        (tmp_workspace / "f1.py").write_text("def hello():\n    pass", encoding="utf-8")
        result = await handle_grep_search({"pattern": "zzzzz", "path": str(tmp_workspace)}, workspace=tmp_workspace)
        assert "no match" in result.lower() or "not found" in result.lower() or result.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_file_ops.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'novare.tools.file_ops'`

- [ ] **Step 3: Write implementation**

```python
"""novare/tools/file_ops.py — 文件操作工具"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path


async def handle_read_file(args: dict, workspace: Path = Path(".")) -> str:
    path = Path(args["path"])
    if not path.exists():
        return f"Error: File not found: {path}"
    if path.is_dir():
        return f"Error: Is a directory: {path}"
    try:
        content = path.read_text(encoding="utf-8")
        return content
    except Exception as e:
        return f"Error reading file: {e}"


async def handle_write_file(args: dict, workspace: Path = Path(".")) -> str:
    path = Path(args["path"])
    content = args["content"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"OK: Written {len(content)} characters to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


async def handle_edit_file(args: dict, workspace: Path = Path(".")) -> str:
    path = Path(args["path"])
    old_string = args["old_string"]
    new_string = args["new_string"]

    if not path.exists():
        return f"Error: File not found: {path}"

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    if old_string not in content:
        return f"Error: old_string not found in {path}"

    count = content.count(old_string)
    new_content = content.replace(old_string, new_string, 1)

    try:
        path.write_text(new_content, encoding="utf-8")
        extra = f" ({count - 1} remaining)" if count > 1 else ""
        return f"OK: Replaced old_string in {path}{extra}"
    except Exception as e:
        return f"Error writing file: {e}"


async def handle_glob_search(args: dict, workspace: Path = Path(".")) -> str:
    pattern = args["pattern"]
    search_path = Path(args.get("path", str(workspace)))

    if not search_path.exists():
        return f"Error: Path not found: {search_path}"

    matches = []
    for root, dirs, files in os.walk(search_path):
        # 跳过隐藏目录和 .git
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, search_path)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f, pattern):
                matches.append(rel)

    if not matches:
        return "No files found matching pattern."

    matches.sort()
    return "\n".join(matches)


async def handle_grep_search(args: dict, workspace: Path = Path(".")) -> str:
    pattern = args["pattern"]
    search_path = Path(args.get("path", str(workspace)))
    glob_filter = args.get("glob", None)

    if not search_path.exists():
        return f"Error: Path not found: {search_path}"

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"

    results = []
    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            if glob_filter and not fnmatch.fnmatch(f, glob_filter):
                continue
            full = os.path.join(root, f)
            try:
                text = open(full, "r", encoding="utf-8", errors="ignore").read()
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        rel = os.path.relpath(full, search_path)
                        results.append(f"{rel}:{i}: {line.strip()}")
            except Exception:
                continue

    if not results:
        return "No matches found."

    return "\n".join(results[:50])  # 限制输出行数
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_file_ops.py -v
```

Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add novare/tools/file_ops.py tests/test_file_ops.py
git commit -m "feat: file_ops — read, write, edit, glob, grep tools"
```

---

## Task 5: Tool Registry + Dispatch

**Files:**
- Create: `novare/tools/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write failing test**

```python
"""tests/test_registry.py"""

import pytest

from novare.tools.registry import ToolRegistry, ToolDef


class TestToolDef:
    def test_to_openai_tool(self):
        tool = ToolDef(
            name="read_file",
            description="Read a file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=None,
        )
        openai_tool = tool.to_openai_tool()
        assert openai_tool["type"] == "function"
        assert openai_tool["function"]["name"] == "read_file"
        assert openai_tool["function"]["description"] == "Read a file"


class TestToolRegistry:
    def test_register_builtin_tools(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "read_file" in names
        assert "write_file" in names
        assert "edit_file" in names
        assert "glob_search" in names
        assert "grep_search" in names

    @pytest.mark.asyncio
    async def test_execute_builtin_tool(self, tmp_workspace):
        (tmp_workspace / "test.txt").write_text("hello", encoding="utf-8")
        registry = ToolRegistry(workspace=tmp_workspace)
        result = await registry.execute("read_file", {"path": str(tmp_workspace / "test.txt")})
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        result = await registry.execute("nonexistent_tool", {})
        assert "Error" in result

    def test_to_openai_tools(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        tools = registry.to_openai_tools()
        assert len(tools) >= 5
        assert all(t["type"] == "function" for t in tools)

    def test_register_mcp_tools(self, tmp_workspace):
        registry = ToolRegistry(workspace=tmp_workspace)
        registry.register_tool(ToolDef(
            name="paper_search",
            description="Search papers",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            handler=None,
            source="mcp:research",
        ))
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "paper_search" in names
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_registry.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'novare.tools.registry'`

- [ ] **Step 3: Write implementation**

```python
"""novare/tools/registry.py — 工具注册表 + 分发"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable

from novare.tools import file_ops

logger = logging.getLogger("novare.tools")


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[[dict], Awaitable[str]] | None
    source: str = "builtin"  # "builtin" | "mcp:<server_name>"

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# 内置工具定义
_BUILTIN_TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": "读取文件内容。返回文件的完整文本。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件的绝对或相对路径"},
            },
            "required": ["path"],
        },
        "handler": file_ops.handle_read_file,
    },
    {
        "name": "write_file",
        "description": "创建或覆盖写入文件。会自动创建父目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
        "handler": file_ops.handle_write_file,
    },
    {
        "name": "edit_file",
        "description": "编辑文件：将 old_string 替换为 new_string。old_string 必须在文件中精确匹配。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "要替换的原始文本"},
                "new_string": {"type": "string", "description": "替换后的文本"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        "handler": file_ops.handle_edit_file,
    },
    {
        "name": "glob_search",
        "description": "按模式搜索文件名。支持 glob 模式如 *.py, **/*.md。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式"},
                "path": {"type": "string", "description": "搜索目录（默认当前目录）"},
            },
            "required": ["pattern"],
        },
        "handler": file_ops.handle_glob_search,
    },
    {
        "name": "grep_search",
        "description": "在文件内容中搜索正则表达式。返回匹配的行及文件路径和行号。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "搜索目录（默认当前目录）"},
                "glob": {"type": "string", "description": "文件名过滤（如 *.py）"},
            },
            "required": ["pattern"],
        },
        "handler": file_ops.handle_grep_search,
    },
]


class ToolRegistry:
    def __init__(self, workspace: Path = Path(".")):
        self.workspace = workspace
        self._tools: dict[str, ToolDef] = {}
        self._register_builtins()

    def _register_builtins(self):
        for t in self._BUILTIN_LIST:
            self._tools[t["name"]] = ToolDef(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
                handler=t["handler"],
                source="builtin",
            )

    @property
    def _BUILTIN_LIST(self):
        return _BUILTIN_TOOLS

    def register_tool(self, tool: ToolDef):
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s (source=%s)", tool.name, tool.source)

    def list_tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict]:
        return [t.to_openai_tool() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"

        if tool.handler is None:
            return f"Error: Tool '{name}' has no handler (MCP tool not connected)"

        try:
            result = await tool.handler(arguments, workspace=self.workspace)
            return result
        except Exception as e:
            logger.exception("Tool execution error: %s", name)
            return f"Error executing {name}: {e}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_registry.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add novare/tools/registry.py tests/test_registry.py
git commit -m "feat: tool registry — built-in tools + MCP tool registration + dispatch"
```

---

## Task 6: MCP Client

**Files:**
- Create: `novare/mcp_client.py`
- Create: `tests/test_mcp_client.py`

- [ ] **Step 1: Write failing test**

```python
"""tests/test_mcp_client.py"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from novare.mcp_client import McpClient


class TestMcpClientInit:
    def test_create_client(self):
        client = McpClient(command="python", args=["server.py"])
        assert client.command == "python"
        assert client.args == ["server.py"]
        assert client._process is None


class TestJsonRpc:
    def test_build_request(self):
        from novare.mcp_client import build_jsonrpc_request
        req = build_jsonrpc_request("tools/list", {})
        assert req["jsonrpc"] == "2.0"
        assert req["method"] == "tools/list"
        assert "id" in req

    def test_build_request_with_params(self):
        from novare.mcp_client import build_jsonrpc_request
        req = build_jsonrpc_request("tools/call", {"name": "echo", "arguments": {"message": "hi"}})
        assert req["params"]["name"] == "echo"

    def test_parse_response(self):
        from novare.mcp_client import parse_jsonrpc_response
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        result = parse_jsonrpc_response(raw)
        assert result["tools"] == []


class TestMcpClientMock:
    @pytest.mark.asyncio
    async def test_discover_tools(self):
        client = McpClient(command="echo", args=[])
        # Mock the process communication
        client._process = AsyncMock()
        client._write = AsyncMock()
        client._read_response = AsyncMock(return_value={
            "tools": [
                {"name": "paper_search", "description": "Search papers", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
                {"name": "paper_parse", "description": "Parse paper", "inputSchema": {"type": "object", "properties": {"paper_id": {"type": "string"}}}},
            ]
        })
        client._request_id = 1

        tools = await client.list_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "paper_search"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_mcp_client.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'novare.mcp_client'`

- [ ] **Step 3: Write implementation**

```python
"""novare/mcp_client.py — MCP stdio 客户端"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("novare.mcp")


def build_jsonrpc_request(method: str, params: dict, req_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params,
    }


def parse_jsonrpc_response(raw: str) -> dict:
    data = json.loads(raw)
    if "error" in data:
        raise RuntimeError(f"JSON-RPC error: {data['error']}")
    return data.get("result", {})


class McpClient:
    """MCP stdio 客户端 — 通过 stdin/stdout 与 MCP Server 通信"""

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        self.command = command
        self.args = args or []
        self.env = env
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._initialized = False

    async def connect(self):
        import os
        full_env = {**os.environ, **(self.env or {})}
        self._process = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )
        logger.info("MCP server started: %s %s (pid=%s)", self.command, self.args, self._process.pid)

        # initialize
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "novare", "version": "0.1.0"},
        })
        # initialized notification (no response expected)
        await self._send_notification("notifications/initialized")
        self._initialized = True
        logger.info("MCP server initialized")

    async def list_tools(self) -> list[dict]:
        result = await self._send_request("tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        # MCP tool result format
        content = result.get("content", [])
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item["text"])
        return "\n".join(texts) if texts else json.dumps(result)

    async def close(self):
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            logger.info("MCP server stopped")

    async def _send_request(self, method: str, params: dict) -> dict:
        self._request_id += 1
        req = build_jsonrpc_request(method, params, self._request_id)
        await self._write(req)
        return await self._read_response()

    async def _send_notification(self, method: str, params: dict | None = None):
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            notification["params"] = params
        await self._write(notification)

    async def _write(self, data: dict):
        payload = json.dumps(data, ensure_ascii=False)
        message = f"Content-Length: {len(payload.encode('utf-8'))}\r\n\r\n{payload}"
        self._process.stdin.write(message.encode("utf-8"))
        await self._process.stdin.drain()
        logger.debug("MCP → %s", data.get("method", f"id={data.get('id')}"))

    async def _read_response(self) -> dict:
        # 读取 Content-Length 头
        header = await self._read_line()
        while header.strip() == "":
            header = await self._read_line()

        content_length = 0
        while header:
            if header.lower().startswith("content-length:"):
                content_length = int(header.split(":", 1)[1].strip())
            line = await self._read_line()
            if line.strip() == "":
                break
            header = line

        if content_length == 0:
            raise RuntimeError("MCP: No Content-Length in response")

        body = await self._read_exact(content_length)
        result = parse_jsonrpc_response(body)
        logger.debug("MCP ← id=%s", result.get("id"))
        return result

    async def _read_line(self) -> str:
        line = await self._process.stdout.readline()
        return line.decode("utf-8") if line else ""

    async def _read_exact(self, n: int) -> str:
        data = b""
        while len(data) < n:
            chunk = await self._process.stdout.read(n - len(data))
            if not chunk:
                raise RuntimeError("MCP: Connection closed")
            data += chunk
        return data.decode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_mcp_client.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add novare/mcp_client.py tests/test_mcp_client.py
git commit -m "feat: mcp_client — stdio JSON-RPC client for MCP servers"
```

---

## Task 7: Agent Loop

**Files:**
- Create: `novare/agent_loop.py`
- Create: `tests/test_agent_loop.py`

- [ ] **Step 1: Write failing test**

```python
"""tests/test_agent_loop.py"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from novare.agent_loop import AgentLoop
from novare.llm_client import LLMResponse, ToolCall
from novare.tools.registry import ToolRegistry, ToolDef


class TestAgentLoop:
    def _make_loop(self, responses: list[LLMResponse], tool_handler=None):
        llm = AsyncMock()
        llm.chat = AsyncMock(side_effect=responses)
        llm.close = AsyncMock()

        registry = ToolRegistry()
        if tool_handler:
            # 覆盖特定工具的 handler
            for name, handler in tool_handler.items():
                registry.register_tool(ToolDef(
                    name=name,
                    description=f"test {name}",
                    parameters={"type": "object", "properties": {}},
                    handler=handler,
                ))

        return AgentLoop(
            llm_client=llm,
            tool_registry=registry,
            system_prompt="You are a test assistant.",
        )

    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        loop = self._make_loop([
            LLMResponse(content="Hello!", tool_calls=[], stop_reason="stop", usage={}),
        ])
        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "Hi")
        assert result == "Hello!"
        assert len(session.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self):
        async def mock_echo(args, workspace=None):
            return f"Echo: {args.get('message', '')}"

        loop = self._make_loop(
            [
                LLMResponse(content="", tool_calls=[
                    ToolCall(id="call_1", name="echo", arguments={"message": "test"})
                ], stop_reason="tool_calls", usage={}),
                LLMResponse(content="Done!", tool_calls=[], stop_reason="stop", usage={}),
            ],
            tool_handler={"echo": mock_echo},
        )
        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "Echo test")
        assert result == "Done!"
        # user + assistant(tool_calls) + tool_result + assistant(final)
        assert len(session.messages) == 4

    @pytest.mark.asyncio
    async def test_max_iterations_returns_fallback(self):
        # Create infinite tool call loop
        call_count = 0
        async def forever_loop(args, workspace=None):
            return "ok"

        responses = []
        for i in range(25):  # more than max_iterations=20
            responses.append(LLMResponse(content="", tool_calls=[
                ToolCall(id=f"call_{i}", name="echo", arguments={"message": str(i)})
            ], stop_reason="tool_calls", usage={}))

        loop = self._make_loop(responses, tool_handler={"echo": forever_loop})
        loop.max_iterations = 20

        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "go")
        assert "最大迭代" in result or "迭代" in result or "重试" in result

    @pytest.mark.asyncio
    async def test_tool_error_is_reported_to_llm(self):
        async def failing_tool(args, workspace=None):
            raise ValueError("something broke")

        loop = self._make_loop(
            [
                LLMResponse(content="", tool_calls=[
                    ToolCall(id="call_1", name="fail_tool", arguments={})
                ], stop_reason="tool_calls", usage={}),
                LLMResponse(content="Tool had an error", tool_calls=[], stop_reason="stop", usage={}),
            ],
            tool_handler={"fail_tool": failing_tool},
        )
        from novare.session import Session
        session = Session()
        result = await loop.run_turn(session, "break it")
        assert result == "Tool had an error"
        # Check tool result message contains error info
        tool_msg = session.messages[2]
        assert "Error" in tool_msg["content"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'novare.agent_loop'`

- [ ] **Step 3: Write implementation**

```python
"""novare/agent_loop.py — 核心 agent 循环"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novare.llm_client import LLMClient
    from novare.tools.registry import ToolRegistry

logger = logging.getLogger("novare.loop")


class AgentLoop:
    """等价于 Claw Code 的 ConversationRuntime.run_turn()"""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        system_prompt: str = "",
        max_iterations: int = 20,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations

    async def run_turn(self, session, user_input: str) -> str:
        """执行一轮对话：用户输入 → LLM → 工具循环 → 最终回答"""
        from novare.session import Session

        session.add_user_message(user_input)

        for iteration in range(self.max_iterations):
            # 构建消息
            messages = self._build_messages(session)

            # 调用 LLM
            tools = self.tool_registry.to_openai_tools()
            response = await self.llm_client.chat(messages, tools=tools)

            # 如果没有工具调用，返回最终回答
            if not response.tool_calls:
                session.add_assistant_message(response.content)
                return response.content

            # 有工具调用：记录 assistant 消息（含 tool_calls）
            tool_calls_dicts = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in response.tool_calls
            ]
            session.add_assistant_message(response.content or "", tool_calls=tool_calls_dicts)

            # 执行每个工具调用
            for tc in response.tool_calls:
                logger.info("Tool call: %s(%s)", tc.name, tc.arguments)
                try:
                    result = await self.tool_registry.execute(tc.name, tc.arguments)
                except Exception as e:
                    logger.exception("Tool error: %s", tc.name)
                    result = f"Error: {e}"
                session.add_tool_result(tc.id, result)
                logger.debug("Tool result: %s → %d chars", tc.name, len(result))

        return "达到最大迭代次数（{}），请简化问题后重试。".format(self.max_iterations)

    def _build_messages(self, session) -> list[dict]:
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(session.messages)
        return messages
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add novare/agent_loop.py tests/test_agent_loop.py
git commit -m "feat: agent_loop — core agent loop (ConversationRuntime equivalent)"
```

---

## Task 8: CLI Entry Point

**Files:**
- Create: `novare/cli.py`

- [ ] **Step 1: Write cli.py**

```python
"""novare/cli.py — 交互式 REPL 入口"""

from __future__ import annotations

import asyncio
import logging
import sys

from novare.config import NovareConfig
from novare.llm_client import LLMClient
from novare.session import Session
from novare.tools.registry import ToolRegistry
from novare.mcp_client import McpClient
from novare.agent_loop import AgentLoop


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("novare")

    # 加载配置
    config = NovareConfig.load()
    if not config.api_key:
        print("Error: NOVARE_API_KEY not set.", file=sys.stderr)
        print("Set it via: export NOVARE_API_KEY=your-key", file=sys.stderr)
        sys.exit(1)

    logger.info("Novare starting — model=%s, workspace=%s", config.model, config.workspace)

    # 初始化 LLM 客户端
    llm_client = LLMClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )

    # 初始化工具注册表
    tool_registry = ToolRegistry(workspace=config.workspace)

    # 连接 MCP 服务器（研究工具）
    mcp_clients: list[McpClient] = []
    for name, srv_config in config.mcp_servers.items():
        logger.info("Connecting to MCP server: %s", name)
        client = McpClient(
            command=srv_config.command,
            args=srv_config.args,
            env=srv_config.env,
        )
        try:
            await client.connect()
            tools = await client.list_tools()
            for tool in tools:
                tool_name = tool["name"]
                # 注册 MCP 工具到 registry
                from novare.tools.registry import ToolDef

                async def make_handler(c: McpClient, tn: str):
                    async def handler(args, workspace=None):
                        return await c.call_tool(tn, args)
                    return handler

                handler = await make_handler(client, tool_name)
                tool_registry.register_tool(ToolDef(
                    name=tool_name,
                    description=tool.get("description", ""),
                    parameters=tool.get("inputSchema", {"type": "object", "properties": {}}),
                    handler=handler,
                    source=f"mcp:{name}",
                ))
            logger.info("MCP server '%s': %d tools registered", name, len(tools))
            mcp_clients.append(client)
        except Exception as e:
            logger.error("Failed to connect to MCP server '%s': %s", name, e)

    # 创建 AgentLoop
    agent = AgentLoop(
        llm_client=llm_client,
        tool_registry=tool_registry,
        system_prompt=config.system_prompt,
    )

    # 创建默认 session
    session = Session(workspace=config.workspace)

    # REPL
    print("Novare 科研智能体 (输入 /help 查看命令)")
    print(f"模型: {config.model} | 工具: {len(tool_registry.list_tools())} 个")
    print("-" * 50)

    try:
        while True:
            try:
                user_input = input("\nnovare> ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            # 内置命令
            if user_input in ("/exit", "/quit", "/q"):
                break
            elif user_input == "/help":
                _print_help()
                continue
            elif user_input == "/sessions":
                sessions = Session.list_sessions(workspace=config.workspace)
                for s in sessions:
                    marker = " ← current" if s == session.session_id else ""
                    print(f"  {s}{marker}")
                continue
            elif user_input.startswith("/session "):
                sid = user_input.split(" ", 1)[1].strip()
                try:
                    session = Session.load(sid, workspace=config.workspace)
                    print(f"Loaded session: {session.session_id} ({len(session.messages)} messages)")
                except FileNotFoundError:
                    print(f"Session not found: {sid}")
                continue
            elif user_input == "/new":
                session = Session(workspace=config.workspace)
                print(f"New session: {session.session_id}")
                continue
            elif user_input.startswith("/"):
                print(f"Unknown command: {user_input}. Type /help for available commands.")
                continue

            # 正常对话
            try:
                result = await agent.run_turn(session, user_input)
                print(f"\n{result}")
                session.save()
            except KeyboardInterrupt:
                print("\n[Interrupted]")
            except Exception as e:
                logger.exception("Error in turn")
                print(f"\nError: {e}")
    finally:
        # 清理
        for client in mcp_clients:
            await client.close()
        await llm_client.close()
        session.save()
        print("\n再见！")


def _print_help():
    print("""
Novare 命令:
  /help          显示此帮助
  /sessions      列出所有会话
  /session <id>  加载指定会话
  /new           创建新会话
  /exit          退出

直接输入文字开始对话，Novare 会自动调用工具完成科研任务。
""")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
git add novare/cli.py
git commit -m "feat: cli — interactive REPL with session management"
```

---

## Task 9: Integration Test + Smoke Test

**Files:**
- Create: `tests/conftest.py` (update if needed)
- Create: `novare_test_harness.py`

- [ ] **Step 1: Create integration test harness**

```python
"""novare_test_harness.py — 手动集成测试脚本

用法: python novare_test_harness.py
需要: NOVARE_API_KEY 环境变量
"""

import asyncio
import sys
import os

# 确保可以 import novare
sys.path.insert(0, os.path.dirname(__file__))


async def test_llm_only():
    """测试 1: 纯 LLM 对话（无工具）"""
    from novare.config import NovareConfig
    from novare.llm_client import LLMClient
    from novare.session import Session

    config = NovareConfig.load()
    if not config.api_key:
        print("SKIP: NOVARE_API_KEY not set")
        return

    llm = LLMClient(api_key=config.api_key, base_url=config.base_url, model=config.model)
    session = Session()

    resp = await llm.chat([{"role": "user", "content": "1+1等于几？只回答数字。"}])
    print(f"LLM 响应: {resp.content}")
    assert resp.content.strip() == "2", f"Expected '2', got '{resp.content}'"
    print("PASS: test_llm_only")
    await llm.close()


async def test_tool_dispatch():
    """测试 2: 工具分发（不调 LLM，直接执行工具）"""
    from novare.tools.registry import ToolRegistry
    from pathlib import Path

    registry = ToolRegistry(workspace=Path("."))

    # 测试 glob_search
    result = await registry.execute("glob_search", {"pattern": "*.py", "path": "."})
    print(f"glob_search 结果: {result[:200]}")
    assert "*.py" in result or "novare" in result
    print("PASS: test_tool_dispatch")


async def test_full_agent_loop():
    """测试 3: 完整 agent 循环（LLM + 工具）"""
    from novare.config import NovareConfig
    from novare.llm_client import LLMClient
    from novare.session import Session
    from novare.tools.registry import ToolRegistry
    from novare.agent_loop import AgentLoop

    config = NovareConfig.load()
    if not config.api_key:
        print("SKIP: NOVARE_API_KEY not set")
        return

    llm = LLMClient(api_key=config.api_key, base_url=config.base_url, model=config.model)
    registry = ToolRegistry(workspace=Path(config.workspace))
    agent = AgentLoop(llm_client=llm, tool_registry=registry, system_prompt=config.system_prompt)
    session = Session(workspace=Path(config.workspace))

    result = await agent.run_turn(session, "用 glob_search 列出当前目录下的所有 Python 文件")
    print(f"Agent 响应: {result}")
    print("PASS: test_full_agent_loop")
    await llm.close()


async def main():
    print("=" * 50)
    print("Novare Integration Tests")
    print("=" * 50)

    await test_llm_only()
    print()
    await test_tool_dispatch()
    print()
    await test_full_agent_loop()
    print()
    print("All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run all unit tests**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/ -v
```

Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add novare_test_harness.py
git commit -m "feat: integration test harness for novare"
```

---

## Task 10: Run + Verify End-to-End

- [ ] **Step 1: Run unit tests**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m pytest tests/ -v --tb=short
```

Expected: All tests pass

- [ ] **Step 2: Run integration test (optional, needs API key)**

```bash
cd d:/project/research-agent
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe novare_test_harness.py
```

- [ ] **Step 3: Start novare CLI (optional, needs API key)**

```bash
cd d:/project/research-agent
export NOVARE_API_KEY=your-key-here
d:/project/research-agent/mcp-server/.venv/Scripts/python.exe -m novare.cli
```

Expected: Interactive REPL starts, can chat and use tools

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: novare MVP — complete agent loop with LLM + MCP tools + file ops"
```
