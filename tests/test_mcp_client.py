"""tests/test_mcp_client.py"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from novare.mcp_client import McpClient


class TestMcpClientInit:
    def test_create_client(self):
        client = McpClient(command="python", args=["server.py"])
        assert client._params.command == "python"
        assert client._params.args == ["server.py"]
        assert client._session is None


class TestMcpClientMock:
    @pytest.mark.asyncio
    async def test_list_tools(self):
        client = McpClient(command="echo", args=[])
        client._tools = [
            {"name": "paper_search", "description": "Search papers", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
            {"name": "paper_parse", "description": "Parse paper", "inputSchema": {"type": "object", "properties": {"paper_id": {"type": "string"}}}},
        ]
        tools = await client.list_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "paper_search"

    @pytest.mark.asyncio
    async def test_call_tool(self):
        client = McpClient(command="echo", args=[])
        mock_session = AsyncMock()
        mock_content = MagicMock()
        mock_content.text = "search result"
        mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[mock_content]))
        client._session = mock_session

        result = await client.call_tool("paper_search", {"query": "test"})
        assert result == "search result"
        mock_session.call_tool.assert_called_once_with("paper_search", {"query": "test"})

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        client = McpClient(command="echo", args=[])
        with pytest.raises(RuntimeError, match="not connected"):
            await client.call_tool("test", {})
