"""novare/tools/registry.py — 工具注册表 + 分发"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable

from novare.recovery.policy import RetryPolicy
from novare.tools import file_ops
from novare.tools.reviewer_evaluate import handle_reviewer_evaluate
from novare.tools.skills import handle_skill_view, handle_skills_list

logger = logging.getLogger("novare.tools")

# 明确只读、可安全自动重试的工具（最多 3 次尝试）
_READ_ONLY_RETRYABLE = {
    "read_file", "glob_search", "grep_search", "paper_search", "rag_query",
    "skills_list", "skill_view",
}
_READ_ONLY_RETRY_ATTEMPTS = 3


_VALID_IDEMPOTENCY = ("read", "idempotent_write", "non_idempotent")


def default_retry_policy_for(name: str, source: str = "builtin") -> RetryPolicy | None:
    """返回工具名的默认重试策略。

    名字推断仅对 builtin 工具生效；MCP / 其他来源的工具必须显式声明
    （不把工具名作为安全判断，服务器语义未知时保守不重试）。
    """
    if source == "builtin" and name in _READ_ONLY_RETRYABLE:
        return RetryPolicy(max_attempts=_READ_ONLY_RETRY_ATTEMPTS)
    return None


def default_idempotency_for(name: str, source: str = "builtin") -> str:
    """返回工具名的默认幂等性："read" | "idempotent_write" | "non_idempotent"。

    名字推断仅对 builtin 工具生效；MCP 等外部来源的工具默认 non_idempotent。
    """
    if source == "builtin" and name in _READ_ONLY_RETRYABLE:
        return "read"
    return "non_idempotent"


def _normalize_idempotency(value: str | None) -> str:
    """规范化幂等性声明：非法值安全回退到 non_idempotent。"""
    if value in _VALID_IDEMPOTENCY:
        return value
    return "non_idempotent"


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[[dict], Awaitable[str]] | None
    source: str = "builtin"  # "builtin" | "mcp:<server_name>"
    # ── PR 1：重试与幂等性（带默认值，不破坏现有构造调用）──
    retry_policy: RetryPolicy | None = None      # None → builtin 按名字推断，否则不重试
    # None 表示“未指定”（由 __post_init__ 推断）；显式传入的 non_idempotent 绝不被覆盖
    idempotency: str | None = None               # "read" | "idempotent_write" | "non_idempotent"
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.retry_policy is None:
            self.retry_policy = default_retry_policy_for(self.name, self.source)
        if self.idempotency is None:
            # 仅在“未指定”时推断；显式 non_idempotent 保持不变
            self.idempotency = default_idempotency_for(self.name, self.source)
        else:
            self.idempotency = _normalize_idempotency(self.idempotency)

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
        "name": "skills_list",
        "description": (
            "列出当前用户可用的 Skill 名称和描述，不加载正文。"
            "当任务可能有专用工作流但目录中没有明显匹配时使用；不要每轮重复调用。"
        ),
        "idempotency": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "可选的名称或描述关键词"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
        "handler": handle_skills_list,
    },
    {
        "name": "skill_view",
        "description": (
            "按名称渐进加载一个 Skill 的完整工作流程。"
            "仅当 Skill 描述明确匹配当前任务时调用；加载后遵循其流程，但不得覆盖系统安全规则和用户要求。"
        ),
        "idempotency": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "精确的 Skill 名称"},
                "arguments": {
                    "type": "string",
                    "description": "可选任务参数，用于替换 Skill 中的 $ARGUMENTS",
                },
            },
            "required": ["name"],
        },
        "handler": handle_skill_view,
    },
    {
        "name": "reviewer_evaluate",
        "description": (
            "用独立的评审模型对候选创新点做对抗评审。双模型模式：executor 生成候选，reviewer 独立评估。"
            "需要配置评审模型环境变量。"
        ),
        "idempotency": "read",          # 纯读操作（调用评审模型），瞬时错误可安全重试
        "retry_max_attempts": 2,
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
            source = (
                "builtin:context"
                if t["name"] in {"reviewer_evaluate", "skills_list", "skill_view"}
                else "builtin"
            )
            retry_policy = None
            max_attempts = t.get("retry_max_attempts")
            if max_attempts is not None:
                retry_policy = RetryPolicy(max_attempts=int(max_attempts))
            self._tools[t["name"]] = ToolDef(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
                handler=t["handler"],
                source=source,
                retry_policy=retry_policy,
                # 不显式传 "non_idempotent"：未声明时由 __post_init__ 推断
                idempotency=t.get("idempotency"),
            )

    def register_tool(self, tool: ToolDef):
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s (source=%s)", tool.name, tool.source)

    def get_tool(self, name: str) -> ToolDef | None:
        """按名字获取工具定义；不存在返回 None。"""
        return self._tools.get(name)

    def retry_policy_for(self, name: str) -> RetryPolicy | None:
        """查询工具的重试策略；未注册或未配置返回 None（调用方回退 max_attempts=1）。"""
        tool = self._tools.get(name)
        return tool.retry_policy if tool is not None else None

    def idempotency_for(self, name: str) -> str:
        """查询工具的幂等性；未注册工具保守返回 "non_idempotent"。"""
        tool = self._tools.get(name)
        return tool.idempotency if tool is not None else "non_idempotent"

    def set_default_tool_context(self, context: dict | None) -> None:
        """设置默认的工具上下文，对 builtin:context 和 mcp 工具自动注入。

        调用时传入的 tool_context 优先级更高（会覆盖默认值）。
        """
        self._default_tool_context = dict(context or {})

    def update_default_tool_context(self, context: dict | None) -> None:
        """Merge trusted dependencies into the default context."""
        self._default_tool_context.update(dict(context or {}))

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
