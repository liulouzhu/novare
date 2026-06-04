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
