"""novare/tool_result.py — 统一工具结果解析

提供 parse_tool_result() 函数，供 agent_loop / agent_service / task_state 共用。
JSON parse 失败时降级到旧的 startswith 检测，确保向后兼容。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class ParsedToolResult:
    """统一解析后的工具结果"""

    ok: bool
    summary: str
    data: dict | list | None
    sources: list
    providers: list
    warnings: list[str]
    error: str | None
    is_json: bool
    raw: str


def parse_tool_result(result: str) -> ParsedToolResult:
    """解析工具返回的 JSON 字符串。

    JSON parse 失败时返回 is_json=False + 降级 ok 判断。
    所有消费方（agent_loop / agent_service / task_state）共用此函数，
    避免各自实现解析逻辑不一致。
    """
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "ok" in parsed:
            return ParsedToolResult(
                ok=bool(parsed["ok"]),
                summary=str(parsed.get("summary", "")),
                data=parsed.get("data"),
                sources=parsed.get("sources", []),
                providers=parsed.get("providers", []),
                warnings=[str(w) for w in parsed.get("warnings", [])],
                error=parsed.get("error"),
                is_json=True,
                raw=result,
            )
    except (json.JSONDecodeError, TypeError):
        pass

    # 降级：非 JSON 结果，用旧的前缀匹配
    is_error = (
        result.startswith("Error")
        or result.startswith("错误")
        or result.startswith("搜索失败")
    )
    return ParsedToolResult(
        ok=not is_error,
        summary=result[:200],
        data=None,
        sources=[],
        providers=[],
        warnings=[],
        error=result if is_error else None,
        is_json=False,
        raw=result,
    )
