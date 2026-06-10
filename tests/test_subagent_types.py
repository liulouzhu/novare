"""tests/test_subagent_types.py — 子智能体数据结构测试"""

import pytest
from novare.subagents.types import (
    SubagentType,
    SubagentStatus,
    SubagentInput,
    SubagentOutput,
    SubagentRecord,
    get_allowlist,
    SUBAGENT_TOOL_ALLOWLISTS,
)


class TestSubagentType:
    def test_enum_values(self):
        assert SubagentType.SEARCH == "search"
        assert SubagentType.ANALYZER == "analyzer"
        assert SubagentType.WRITER == "writer"
        assert SubagentType.EXPLORER == "explorer"
        assert SubagentType.GENERAL == "general"

    def test_from_string(self):
        assert SubagentType("search") == SubagentType.SEARCH
        assert SubagentType("general") == SubagentType.GENERAL

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            SubagentType("invalid")


class TestSubagentStatus:
    def test_all_statuses(self):
        assert SubagentStatus.PENDING == "pending"
        assert SubagentStatus.RUNNING == "running"
        assert SubagentStatus.COMPLETED == "completed"
        assert SubagentStatus.FAILED == "failed"
        assert SubagentStatus.CANCELLED == "cancelled"


class TestSubagentInput:
    def test_basic_creation(self):
        inp = SubagentInput(subagent_type=SubagentType.SEARCH, task="搜索论文")
        assert inp.subagent_type == SubagentType.SEARCH
        assert inp.task == "搜索论文"
        assert inp.max_iterations == 16
        assert inp.context is None

    def test_empty_task_raises(self):
        with pytest.raises(ValueError, match="task 不能为空"):
            SubagentInput(subagent_type=SubagentType.SEARCH, task="")

    def test_whitespace_task_raises(self):
        with pytest.raises(ValueError, match="task 不能为空"):
            SubagentInput(subagent_type=SubagentType.SEARCH, task="   ")

    def test_invalid_max_iterations(self):
        with pytest.raises(ValueError, match="max_iterations"):
            SubagentInput(subagent_type=SubagentType.SEARCH, task="test", max_iterations=0)


class TestSubagentOutput:
    def test_to_dict(self):
        output = SubagentOutput(
            subagent_id="sa-test123",
            status=SubagentStatus.COMPLETED,
            result="Found 5 papers",
            tool_calls_made=3,
            elapsed_seconds=12.5,
        )
        d = output.to_dict()
        assert d["subagent_id"] == "sa-test123"
        assert d["status"] == "completed"
        assert d["result"] == "Found 5 papers"
        assert d["tool_calls_made"] == 3
        assert d["elapsed_seconds"] == 12.5
        assert d["error"] is None


class TestSubagentRecord:
    def test_default_status(self):
        record = SubagentRecord(subagent_id="sa-abc", type=SubagentType.SEARCH, task="test")
        assert record.status == SubagentStatus.PENDING
        assert record.result == ""
        assert record.error is None
        assert record.tool_calls_made == 0

    def test_elapsed_calculation(self):
        import time
        record = SubagentRecord(subagent_id="sa-abc", type=SubagentType.SEARCH, task="test")
        assert record.elapsed >= 0
        record.finished_at = record.created_at + 5.0
        assert record.elapsed == pytest.approx(5.0)

    def test_to_output(self):
        record = SubagentRecord(
            subagent_id="sa-abc", type=SubagentType.SEARCH, task="test",
            status=SubagentStatus.COMPLETED, result="done", tool_calls_made=2,
        )
        record.finished_at = record.created_at + 10.0
        output = record.to_output()
        assert output.subagent_id == "sa-abc"
        assert output.status == SubagentStatus.COMPLETED
        assert output.result == "done"

    def test_to_dict(self):
        record = SubagentRecord(subagent_id="sa-abc", type=SubagentType.SEARCH, task="test")
        d = record.to_dict()
        assert d["subagent_id"] == "sa-abc"
        assert d["type"] == "search"
        assert d["status"] == "pending"


class TestToolAllowlists:
    def test_search_has_expected_tools(self):
        allowlist = SUBAGENT_TOOL_ALLOWLISTS[SubagentType.SEARCH]
        assert "paper_search" in allowlist
        assert "innovation_search" in allowlist
        assert "read_file" in allowlist
        assert "write_file" not in allowlist

    def test_analyzer_has_expected_tools(self):
        allowlist = SUBAGENT_TOOL_ALLOWLISTS[SubagentType.ANALYZER]
        assert "code_execute" in allowlist
        assert "rag_query" in allowlist
        assert "paper_search" not in allowlist

    def test_writer_has_write_tools(self):
        allowlist = SUBAGENT_TOOL_ALLOWLISTS[SubagentType.WRITER]
        assert "write_file" in allowlist
        assert "edit_file" in allowlist
        assert "code_execute" not in allowlist

    def test_explorer_is_readonly(self):
        allowlist = SUBAGENT_TOOL_ALLOWLISTS[SubagentType.EXPLORER]
        assert "read_file" in allowlist
        assert "write_file" not in allowlist
        assert "edit_file" not in allowlist

    def test_excluded_tools_not_in_any_allowlist(self):
        excluded = {"spawn_subagent", "check_subagent", "list_subagents", "reviewer_evaluate"}
        for stype, tools in SUBAGENT_TOOL_ALLOWLISTS.items():
            for tool in excluded:
                assert tool not in tools, f"{tool} should not be in {stype.value} allowlist"

    def test_get_allowlist_general_with_names(self):
        all_names = {"read_file", "write_file", "spawn_subagent", "check_subagent", "paper_search"}
        result = get_allowlist(SubagentType.GENERAL, all_names)
        assert "read_file" in result
        assert "write_file" in result
        assert "paper_search" in result
        assert "spawn_subagent" not in result
        assert "check_subagent" not in result

    def test_get_allowlist_general_fallback(self):
        """不传 all_tool_names 时使用保守回退"""
        result = get_allowlist(SubagentType.GENERAL, None)
        assert len(result) > 0
        assert "spawn_subagent" not in result
