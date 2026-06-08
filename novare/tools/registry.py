"""novare/tools/registry.py — 工具注册表 + 分发"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
        for t in _BUILTIN_TOOLS:
            self._tools[t["name"]] = ToolDef(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
                handler=t["handler"],
                source="builtin",
            )

    def register_tool(self, tool: ToolDef):
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s (source=%s)", tool.name, tool.source)

    def list_tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict]:
        return [t.to_openai_tool() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict, tool_context: dict | None = None) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"

        if tool.handler is None:
            return f"Error: Tool '{name}' has no handler (MCP tool not connected)"

        try:
            kwargs = {"workspace": self.workspace}
            if tool.source.startswith("mcp:") and tool_context:
                kwargs.update(tool_context)
            result = await tool.handler(arguments, **kwargs)
            return result
        except Exception as e:
            logger.exception("Tool execution error: %s", name)
            return f"Error executing {name}: {e}"
