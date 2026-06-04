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
