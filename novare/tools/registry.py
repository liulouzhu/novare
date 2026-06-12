"""novare/tools/registry.py — 工具注册表 + 分发"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable

from novare.tools import file_ops
from novare.tools.reviewer_evaluate import handle_reviewer_evaluate

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
    {
        "name": "reviewer_evaluate",
        "description": (
            "用独立的评审模型对候选创新点做对抗评审。双模型模式：executor 生成候选，reviewer 独立评估。"
            "需要配置评审模型环境变量。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "研究主题"},
                "stage": {
                    "type": "string",
                    "enum": ["candidates", "review"],
                    "description": "评审阶段：candidates（评估候选质量）或 review（对执行者的评审做交叉验证）",
                },
                "candidates": {
                    "type": "array",
                    "description": "候选创新点列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "problem": {"type": "string"},
                            "idea": {"type": "string"},
                            "key_difference": {"type": "string"},
                            "expected_value": {"type": "string"},
                            "keywords": {"type": "array", "items": {"type": "string"}},
                            "innovation_level": {"type": "string"},
                        },
                    },
                },
                "executor_review": {
                    "type": "string",
                    "description": "执行者的评审结果摘要（stage='review' 时提供）",
                },
            },
            "required": ["topic", "stage", "candidates"],
        },
        "handler": handle_reviewer_evaluate,
    },
]


# Web 多用户隔离：这些工具的 workspace 可被 tool_context 覆盖
_FILE_TOOLS = {"read_file", "write_file", "edit_file", "glob_search", "grep_search"}


class ToolRegistry:
    def __init__(self, workspace: Path = Path(".")):
        self.workspace = workspace
        self._tools: dict[str, ToolDef] = {}
        self._default_tool_context: dict = {}
        self._register_builtins()

    def _register_builtins(self):
        for t in _BUILTIN_TOOLS:
            # reviewer_evaluate 需要 tool_context（包含 reviewer_llm）
            source = "builtin:context" if t["name"] == "reviewer_evaluate" else "builtin"
            self._tools[t["name"]] = ToolDef(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
                handler=t["handler"],
                source=source,
            )

    def register_tool(self, tool: ToolDef):
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s (source=%s)", tool.name, tool.source)

    def set_default_tool_context(self, context: dict | None) -> None:
        """设置默认的工具上下文，对 builtin:context 和 mcp 工具自动注入。

        调用时传入的 tool_context 优先级更高（会覆盖默认值）。
        """
        self._default_tool_context = dict(context or {})

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
            # Web 多用户隔离：文件类 builtin 工具可用 tool_context["workspace"] 覆盖
            if name in _FILE_TOOLS and tool_context and "workspace" in tool_context:
                kwargs["workspace"] = Path(tool_context["workspace"])
            # MCP 工具和需要上下文的内置工具（如 reviewer_evaluate）传递 tool_context
            needs_context = tool.source.startswith("mcp:") or tool.source == "builtin:context"
            if needs_context:
                merged_context = dict(self._default_tool_context)
                if tool_context:
                    merged_context.update(tool_context)
                kwargs.update(merged_context)
            result = await tool.handler(arguments, **kwargs)
            return result
        except Exception as e:
            logger.exception("Tool execution error: %s", name)
            return f"Error executing {name}: {e}"
