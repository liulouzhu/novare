"""novare/mcp_client.py — MCP stdio 客户端（基于 mcp 库）"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from mcp import ClientSession, StdioServerParameters, stdio_client

logger = logging.getLogger("novare.mcp")


@dataclass
class McpServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class McpClient:
    """MCP stdio 客户端 — 基于 mcp 库的 ClientSession"""

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, cwd: str | None = None):
        self._params = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
            cwd=cwd,
        )
        self._session: ClientSession | None = None
        self._cm = None  # stdio_client context manager
        self._tools: list[dict] = []

    async def connect(self):
        """启动 MCP 服务器并完成握手"""
        self._cm = stdio_client(self._params)
        read_stream, write_stream = await self._cm.__aenter__()
        self._session = ClientSession(read_stream, write_stream)
        await self._session.__aenter__()

        result = await self._session.initialize()
        logger.info("MCP server initialized: %s (v%s)",
                     result.serverInfo.name, result.serverInfo.version)

        # 发现工具
        tools_result = await self._session.list_tools()
        self._tools = [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema if isinstance(t.inputSchema, dict) else {},
            }
            for t in tools_result.tools
        ]
        logger.info("MCP server: %d tools discovered", len(self._tools))

    async def list_tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        if not self._session:
            raise RuntimeError("MCP server not connected")

        result = await self._session.call_tool(name, arguments)
        texts = []
        for item in result.content:
            if hasattr(item, "text"):
                texts.append(item.text)
        return "\n".join(texts) if texts else str(result)

    async def close(self):
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
        except (RuntimeError, Exception) as e:
            logger.debug("MCP session close warning: %s", e)
        try:
            if self._cm:
                await self._cm.__aexit__(None, None, None)
        except (RuntimeError, Exception) as e:
            logger.debug("MCP transport close warning: %s", e)
        logger.info("MCP server closed")
