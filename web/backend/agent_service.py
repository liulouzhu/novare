"""Agent 服务层 — 初始化 Agent 组件 + asyncio.Queue 桥接流式回调"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，以便 import novare / mcp-server 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from uuid import UUID

from novare.agent_loop import AgentLoop  # noqa: E402
from novare.config import NovareConfig  # noqa: E402
from novare.llm_client import LLMClient  # noqa: E402
from novare.mcp_client import McpClient  # noqa: E402
from novare.session import Session, JsonlSessionStore  # noqa: E402
from novare.tools.registry import ToolDef, ToolRegistry  # noqa: E402
from web.backend.db.base import SessionLocal  # noqa: E402
from web.backend.repositories import SessionRepository, MessageRepository  # noqa: E402

logger = logging.getLogger("novare.web")


class AgentService:
    """管理 Agent 生命周期，提供 run_turn 桥接方法"""

    def __init__(self):
        self.config: NovareConfig | None = None
        self.llm_client: LLMClient | None = None
        self.tool_registry: ToolRegistry | None = None
        self.agent: AgentLoop | None = None
        self._mcp_clients: list[McpClient] = []

    async def initialize(self):
        """启动时调用：加载配置、连接 MCP、初始化 Agent"""
        self.config = NovareConfig.load()

        self.llm_client = LLMClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
        )

        self.tool_registry = ToolRegistry(workspace=self.config.workspace)

        # 连接 MCP 服务器并注册工具
        for name, mcp_cfg in self.config.mcp_servers.items():
            logger.info("Connecting MCP server: %s", name)
            try:
                client = McpClient(
                    command=mcp_cfg.command,
                    args=mcp_cfg.args,
                    env=mcp_cfg.env,
                )
                await client.connect()
                raw_tools = await client.list_tools()
                for t in raw_tools:
                    tool_def = ToolDef(
                        name=t["name"],
                        description=t["description"],
                        parameters=t.get("inputSchema", {}),
                        handler=_make_mcp_handler(client, t["name"]),
                        source=f"mcp:{name}",
                    )
                    self.tool_registry.register_tool(tool_def)
                self._mcp_clients.append(client)
                logger.info("Registered %d tools from %s", len(raw_tools), name)
            except Exception:
                logger.exception("Failed to connect MCP server: %s", name)

        self.agent = AgentLoop(
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            system_prompt=self.config.system_prompt,
        )
        logger.info("AgentService initialized (model=%s)", self.config.model)

    async def shutdown(self):
        """关闭时清理资源"""
        for client in self._mcp_clients:
            try:
                await client.close()
            except Exception:
                pass
        if self.llm_client:
            await self.llm_client.close()
        logger.info("AgentService shut down")

    def load_session(self, session_id: str) -> Session:
        """加载或创建会话"""
        try:
            return Session.load(session_id, workspace=self.config.workspace)
        except FileNotFoundError:
            return Session(session_id=session_id, workspace=self.config.workspace)

    def list_sessions(self) -> list[dict]:
        """列出所有会话（简略信息）"""
        sessions = Session.list_sessions(workspace=self.config.workspace)
        result = []
        for sid in sessions:
            try:
                session = Session.load(sid, workspace=self.config.workspace)
                title = _extract_title(session.messages)
                result.append({
                    "session_id": sid,
                    "title": title,
                    "message_count": len(session.messages),
                    "updated_at": _session_updated_time(sid, self.config.workspace),
                })
            except Exception:
                result.append({"session_id": sid, "title": sid, "message_count": 0, "updated_at": ""})
        return result

    def create_session(self) -> Session:
        """创建新会话"""
        return Session(workspace=self.config.workspace)

    def delete_session(self, session_id: str):
        """删除会话"""
        session = Session(session_id=session_id, workspace=self.config.workspace)
        session.delete()

    async def run_turn(
        self,
        session: Session,
        user_input: str,
        queue: asyncio.Queue,
        user_id: str | None = None,
    ):
        """执行一轮对话，通过 queue 将事件推送给 WebSocket

        事件格式：
            {"type": "text_delta", "content": "..."}
            {"type": "reasoning_delta", "content": "..."}
            {"type": "tool_start", "tool": "...", "params": {...}}
            {"type": "tool_end", "tool": "...", "result": "...", "duration": 2.3}
            {"type": "tool_error", "tool": "...", "error": "..."}
            {"type": "done"}
        """
        def on_text(chunk: str):
            # 区分 reasoning 和 content 通过前缀标记（来自 llm_client）
            queue.put_nowait({"type": "text_delta", "content": chunk})

        def on_tool(event: str, name: str, args: dict, result: str | None, duration: float | None):
            if event == "start":
                queue.put_nowait({
                    "type": "tool_start",
                    "tool": name,
                    "params": args,
                })
            elif event == "end":
                queue.put_nowait({
                    "type": "tool_end",
                    "tool": name,
                    "result": result or "",
                    "duration": round(duration or 0, 2),
                })
            elif event == "error":
                queue.put_nowait({
                    "type": "tool_error",
                    "tool": name,
                    "error": result or "Unknown error",
                })

        try:
            # 记录本轮前的消息数，用于提取新增消息
            msgs_before = len(session.messages)

            result = await self.agent.run_turn(
                session, user_input,
                on_text=on_text,
                on_tool=on_tool,
            )

            # ── 持久化到 PostgreSQL（仅当 user_id 存在时） ──
            if user_id:
                try:
                    user_uuid = UUID(user_id)
                    db = SessionLocal()
                    try:
                        # 确保 DB session 存在
                        session_repo = SessionRepository(db, user_uuid)
                        if not session_repo.get_by_id(session.session_id):
                            session_repo.create(session.session_id, title="新会话")

                        # 增量追加本轮新消息到 DB
                        new_messages = session.messages[msgs_before:]
                        if new_messages:
                            msg_repo = MessageRepository(db, user_uuid)
                            for msg in new_messages:
                                msg_repo.add_message(
                                    session_id=session.session_id,
                                    role=msg["role"],
                                    content=msg.get("content"),
                                    tool_calls=msg.get("tool_calls"),
                                    tool_call_id=msg.get("tool_call_id"),
                                )
                        db.commit()
                    except Exception:
                        db.rollback()
                        logger.exception("Failed to persist messages to DB")
                    finally:
                        db.close()
                except Exception:
                    logger.exception("DB persistence error (non-fatal)")

            # ── 持久化到 JSONL（agent loop 需要） ──
            session.save()
            await queue.put({"type": "done"})
            return result
        except Exception as e:
            logger.exception("run_turn failed")
            error_msg = str(e)
            if "ReadTimeout" in type(e).__name__ or "ReadTimeout" in error_msg:
                error_msg = "LLM API 响应超时，请稍后重试（可能是模型处理时间过长）"
            elif "ConnectError" in type(e).__name__ or "Connect" in error_msg:
                error_msg = "无法连接到 LLM API，请检查网络和 API 配置"
            await queue.put({"type": "error", "message": error_msg})
            return ""


def _make_mcp_handler(client: McpClient, tool_name: str):
    """创建 MCP 工具的 handler 闭包，调用 MCP client 的 call_tool"""
    async def handler(arguments: dict, **kwargs) -> str:
        return await client.call_tool(tool_name, arguments)
    return handler


def _extract_title(messages: list[dict]) -> str:
    """从第一条用户消息提取标题"""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content[:60].replace("\n", " ") + ("..." if len(content) > 60 else "")
    return "新会话"


def _session_updated_time(session_id: str, workspace: Path) -> str:
    """获取会话文件的修改时间"""
    import os
    path = workspace / ".novare" / "sessions" / f"{session_id}.jsonl"
    if path.exists():
        mtime = os.path.getmtime(path)
        from datetime import datetime
        return datetime.fromtimestamp(mtime).isoformat()
    return ""
