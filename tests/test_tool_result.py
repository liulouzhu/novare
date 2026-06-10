"""tests/test_tool_result.py — ToolResult helper + parse_tool_result 测试"""

import importlib
import json
import sys
from pathlib import Path

import pytest

from novare.tool_result import ParsedToolResult, parse_tool_result

# 将 mcp-server/tools 加入 path 以导入 result helper
_MCP_TOOLS = str(Path(__file__).resolve().parent.parent / "mcp-server" / "tools")
if _MCP_TOOLS not in sys.path:
    sys.path.insert(0, _MCP_TOOLS)

_result_mod = importlib.import_module("result")
ok = _result_mod.ok
fail = _result_mod.fail
truncate = _result_mod.truncate
truncate_pair = _result_mod.truncate_pair


# ── result.py helper 测试 ────────────────────────────────────

class TestResultHelper:
    """mcp-server/tools/result.py 的 ok/fail 工厂函数测试"""

    def test_ok_basic(self):
        result = json.loads(ok("paper_search", {"total": 5}, summary="找到 5 篇"))
        assert result["schema_version"] == 1
        assert result["tool"] == "paper_search"
        assert result["ok"] is True
        assert result["summary"] == "找到 5 篇"
        assert result["data"] == {"total": 5}
        assert result["sources"] == []
        assert result["providers"] == []
        assert result["warnings"] == []
        assert result["error"] is None

    def test_ok_with_all_fields(self):
        sources = [{"id": "doi:10.1234", "title": "Test Paper"}]
        result = json.loads(ok(
            "rag_query",
            {"results": []},
            summary="检索完成",
            sources=sources,
            providers=["Milvus"],
            warnings=["低置信度"],
        ))
        assert result["sources"] == sources
        assert result["providers"] == ["Milvus"]
        assert result["warnings"] == ["低置信度"]

    def test_fail_basic(self):
        result = json.loads(fail("code_execute", "empty code"))
        assert result["schema_version"] == 1
        assert result["tool"] == "code_execute"
        assert result["ok"] is False
        assert result["error"] == "empty code"
        assert result["data"] is None

    def test_fail_with_data(self):
        data = {"stdout": "err", "stderr": "traceback", "exit_code": 1}
        result = json.loads(fail("code_execute", "exit_code=1", data=data))
        assert result["data"] == data
        assert result["ok"] is False

    def test_schema_version_always_1(self):
        r_ok = json.loads(ok("test", {}, summary="s"))
        r_fail = json.loads(fail("test", "e"))
        assert r_ok["schema_version"] == 1
        assert r_fail["schema_version"] == 1

    def test_truncate_short(self):
        assert truncate("hello", 10) == "hello"

    def test_truncate_long(self):
        result = truncate("a" * 2000, 1000)
        assert len(result) == 1001  # 1000 + "…"
        assert result.endswith("…")

    def test_truncate_empty(self):
        assert truncate("", 100) == ""
        assert truncate(None, 100) == ""

    def test_truncate_pair(self):
        stdout, stderr = truncate_pair("a" * 5000, "b" * 3000)
        assert len(stdout) == 4001  # MAX_STDOUT + "…"
        assert len(stderr) == 2001  # MAX_STDERR + "…"


# ── parse_tool_result 测试 ───────────────────────────────────

class TestParseToolResult:
    """novare/tool_result.py 的 parse_tool_result 测试"""

    def test_parse_valid_ok(self):
        raw = ok("paper_search", {"total": 3}, summary="找到 3 篇",
                 providers=["arxiv"], warnings=["S2 不可用"])
        parsed = parse_tool_result(raw)
        assert parsed.is_json is True
        assert parsed.ok is True
        assert parsed.summary == "找到 3 篇"
        assert parsed.data == {"total": 3}
        assert parsed.providers == ["arxiv"]
        assert parsed.warnings == ["S2 不可用"]
        assert parsed.error is None

    def test_parse_valid_fail(self):
        raw = fail("code_execute", "exit_code=1", data={"stdout": "err"})
        parsed = parse_tool_result(raw)
        assert parsed.is_json is True
        assert parsed.ok is False
        assert parsed.error == "exit_code=1"
        assert parsed.data == {"stdout": "err"}

    def test_parse_legacy_error_prefix(self):
        parsed = parse_tool_result("Error: something broke")
        assert parsed.is_json is False
        assert parsed.ok is False
        assert parsed.error == "Error: something broke"

    def test_parse_legacy_chinese_error(self):
        parsed = parse_tool_result("错误：请提供搜索关键词。")
        assert parsed.is_json is False
        assert parsed.ok is False

    def test_parse_legacy_search_fail(self):
        parsed = parse_tool_result("搜索失败，所有数据源均不可用")
        assert parsed.is_json is False
        assert parsed.ok is False

    def test_parse_legacy_success(self):
        parsed = parse_tool_result("找到 5 篇相关论文：...")
        assert parsed.is_json is False
        assert parsed.ok is True
        assert parsed.error is None

    def test_parse_json_without_ok_field(self):
        parsed = parse_tool_result('{"result": "some data"}')
        assert parsed.is_json is False

    def test_parse_non_dict_json(self):
        parsed = parse_tool_result('[1, 2, 3]')
        assert parsed.is_json is False

    def test_parse_empty_string(self):
        parsed = parse_tool_result("")
        assert parsed.is_json is False
        assert parsed.ok is True

    def test_raw_preserved(self):
        raw = ok("test", {}, summary="s")
        parsed = parse_tool_result(raw)
        assert parsed.raw == raw
