"""tests/test_task_state.py — TaskState 和 TaskStateManager 单元测试"""

import pytest

from novare.task_state import TaskState, TaskStateManager


class TestTaskState:
    """TaskState 数据结构测试"""

    def test_default_state(self):
        state = TaskState()
        assert state.goal == ""
        assert state.completed == []
        assert state.pending == []
        assert state.tools_used == []
        assert state.key_findings == []
        assert state.missing_info == []

    def test_to_dict(self):
        state = TaskState(
            goal="搜索论文",
            completed=["搜索 'Transformer' 找到 10 篇论文"],
            pending=["解析论文"],
            tools_used=["paper_search"],
            key_findings=["Attention Is All You Need"],
            missing_info=[],
        )
        d = state.to_dict()
        assert d["goal"] == "搜索论文"
        assert d["completed"] == ["搜索 'Transformer' 找到 10 篇论文"]
        assert d["pending"] == ["解析论文"]
        assert d["tools_used"] == ["paper_search"]
        assert d["key_findings"] == ["Attention Is All You Need"]
        assert d["missing_info"] == []
        # 返回的是副本，修改不影响原对象
        d["completed"].append("extra")
        assert len(state.completed) == 1

    def test_to_prompt_block_full(self):
        state = TaskState(
            goal="搜索 Transformer 论文",
            completed=["搜索 'attention' 找到 15 篇论文"],
            pending=["解析关键论文", "综合回答"],
            tools_used=["paper_search", "paper_search"],
            key_findings=["Attention Is All You Need (Vaswani et al., 2017)"],
            missing_info=["缺少 2024 年之后的论文"],
        )
        block = state.to_prompt_block()
        assert "[当前任务状态]" in block
        assert "目标：搜索 Transformer 论文" in block
        assert "已完成：" in block
        assert "搜索 'attention' 找到 15 篇论文" in block
        assert "待办：" in block
        assert "解析关键论文" in block
        assert "已用工具：" in block
        assert "关键发现：" in block
        assert "Attention Is All You Need" in block
        assert "缺失信息：" in block
        assert "缺少 2024 年之后的论文" in block

    def test_to_prompt_block_empty(self):
        state = TaskState()
        block = state.to_prompt_block()
        assert "[当前任务状态]" in block
        # 空字段不显示
        assert "已完成：" not in block
        assert "待办：" not in block
        assert "关键发现：" not in block

    def test_tool_summary(self):
        state = TaskState(tools_used=["paper_search", "paper_parse", "paper_search"])
        assert state.tool_summary == "paper_search×2, paper_parse×1"

    def test_tool_summary_empty(self):
        state = TaskState()
        assert state.tool_summary == ""


class TestTaskStateManager:
    """TaskStateManager 生命周期和更新测试"""

    def test_init_turn(self):
        mgr = TaskStateManager()
        assert mgr.state is None

        mgr.init_turn("帮我搜索 Transformer 相关论文")
        assert mgr.state is not None
        assert "Transformer" in mgr.state.goal
        assert len(mgr.state.pending) > 0

    def test_init_turn_infers_search(self):
        mgr = TaskStateManager()
        mgr.init_turn("搜索 Transformer 论文")
        assert any("搜索" in p for p in mgr.state.pending)

    def test_init_turn_infers_parse(self):
        mgr = TaskStateManager()
        mgr.init_turn("帮我解析这篇论文的 PDF")
        assert any("解析" in p for p in mgr.state.pending)

    def test_init_turn_infers_analyze(self):
        mgr = TaskStateManager()
        mgr.init_turn("对比分析 BERT 和 GPT 的差异")
        assert any("分析" in p for p in mgr.state.pending)

    def test_init_turn_default_pending(self):
        mgr = TaskStateManager()
        mgr.init_turn("你好")
        assert mgr.state.pending == ["回答用户问题"]

    def test_clear(self):
        mgr = TaskStateManager()
        mgr.init_turn("test")
        assert mgr.state is not None
        mgr.clear()
        assert mgr.state is None

    def test_update_paper_search(self):
        mgr = TaskStateManager()
        mgr.init_turn("搜索论文")
        mgr.update_from_tool(
            "paper_search",
            {"query": "Transformer attention"},
            "找到 15 篇论文\nTitle: Attention Is All You Need\nTitle: BERT: Pre-training",
        )
        state = mgr.state
        assert "paper_search" in state.tools_used
        assert len(state.completed) == 1
        assert "Transformer attention" in state.completed[0]
        assert "15" in state.completed[0]
        # 提取到论文标题
        assert any("Attention Is All You Need" in f for f in state.key_findings)
        assert any("BERT" in f for f in state.key_findings)

    def test_update_paper_parse(self):
        mgr = TaskStateManager()
        mgr.init_turn("解析论文")
        # 先添加一个待解析的 pending
        mgr.state.pending.append("解析论文 abc")
        mgr.update_from_tool(
            "paper_parse",
            {"paper_id": "arxiv:2301.00001"},
            "Title: Example Paper\nAbstract: ...",
        )
        state = mgr.state
        assert any("arxiv:2301.00001" in c for c in state.completed)
        assert any("已解析" in f for f in state.key_findings)

    def test_update_paper_parse_error(self):
        mgr = TaskStateManager()
        mgr.init_turn("解析论文")
        mgr.update_from_tool(
            "paper_parse",
            {"paper_id": "bad_id"},
            "错误：找不到论文 bad_id",
        )
        state = mgr.state
        assert len(state.completed) == 0  # 失败不算完成
        assert any("bad_id" in m for m in state.missing_info)

    def test_update_rag_query(self):
        mgr = TaskStateManager()
        mgr.init_turn("检索问答")
        mgr.update_from_tool(
            "rag_query",
            {"question": "What is the main contribution of this paper?"},
            "Found 3 relevant chunks...",
        )
        state = mgr.state
        assert any("语义检索" in c for c in state.completed)
        assert "rag_query" in state.tools_used

    def test_update_knowledge_graph(self):
        mgr = TaskStateManager()
        mgr.init_turn("查询图谱")
        mgr.update_from_tool(
            "knowledge_graph",
            {"action": "query"},
            "Found 5 entities...",
        )
        assert any("query" in c for c in mgr.state.completed)

    def test_update_code_execute(self):
        mgr = TaskStateManager()
        mgr.init_turn("执行代码")
        mgr.update_from_tool(
            "code_execute",
            {"code": "print(1)"},
            "1",
        )
        assert any("代码" in c for c in mgr.state.completed)

    def test_update_reviewer_evaluate(self):
        mgr = TaskStateManager()
        mgr.init_turn("评审")
        mgr.update_from_tool(
            "reviewer_evaluate",
            {"topic": "test"},
            '{"score": 8}',
        )
        assert any("评审" in c for c in mgr.state.completed)

    def test_update_innovation_search(self):
        mgr = TaskStateManager()
        mgr.init_turn("创新搜索")
        mgr.update_from_tool(
            "innovation_search",
            {"action": "landscape", "topic": "LLM agents"},
            "Found 10 papers...",
        )
        assert any("landscape" in c for c in mgr.state.completed)

    def test_update_before_init_is_noop(self):
        mgr = TaskStateManager()
        # 未 init_turn 就调 update_from_tool，不应报错
        mgr.update_from_tool("paper_search", {"query": "test"}, "found 5")
        assert mgr.state is None

    def test_tools_used_dedup(self):
        mgr = TaskStateManager()
        mgr.init_turn("test")
        mgr.update_from_tool("paper_search", {"query": "a"}, "found 5")
        mgr.update_from_tool("paper_search", {"query": "b"}, "found 3")
        mgr.update_from_tool("paper_parse", {"paper_id": "x"}, "ok")
        assert mgr.state.tools_used == ["paper_search", "paper_parse"]

    def test_key_findings_fifo(self):
        mgr = TaskStateManager()
        mgr.init_turn("test")
        for i in range(15):
            mgr.record_finding(f"finding_{i}")
        assert len(mgr.state.key_findings) == 10
        # 最早的被淘汰
        assert "finding_0" not in mgr.state.key_findings
        assert "finding_14" in mgr.state.key_findings
        assert "finding_5" in mgr.state.key_findings

    def test_key_findings_dedup(self):
        mgr = TaskStateManager()
        mgr.init_turn("test")
        mgr.record_finding("same finding")
        mgr.record_finding("same finding")
        assert mgr.state.key_findings.count("same finding") == 1

    def test_record_completion_removes_pending(self):
        mgr = TaskStateManager()
        mgr.init_turn("搜索论文并解析")
        # pending 应包含 "搜索相关论文" 和 "解析目标论文"
        initial_pending = list(mgr.state.pending)
        assert len(initial_pending) >= 1

        # 记录搜索完成
        mgr.record_completion("搜索 'Transformer' 找到 10 篇论文")
        # "搜索相关论文" 应该被移除
        assert not any("搜索" in p and "相关论文" in p for p in mgr.state.pending)

    def test_concurrent_safety(self):
        """两个独立 TaskStateManager 实例互不干扰"""
        mgr1 = TaskStateManager()
        mgr2 = TaskStateManager()

        mgr1.init_turn("搜索论文 A")
        mgr2.init_turn("搜索论文 B")

        mgr1.update_from_tool("paper_search", {"query": "A"}, "找到 5 篇")
        mgr2.update_from_tool("paper_search", {"query": "B"}, "找到 10 篇")

        assert "A" in mgr1.state.completed[0]
        assert "B" in mgr2.state.completed[0]
        assert "5" in mgr1.state.completed[0]
        assert "10" in mgr2.state.completed[0]
        # 互不影响
        assert mgr1.state.goal != mgr2.state.goal

    def test_turn_isolation(self):
        """同一 manager 连续两次 init_turn 各自独立"""
        mgr = TaskStateManager()

        mgr.init_turn("第一次任务")
        mgr.update_from_tool("paper_search", {"query": "A"}, "找到 5 篇")
        first_completed = list(mgr.state.completed)

        mgr.init_turn("第二次任务")
        # 新 turn 的状态应该是全新的
        assert mgr.state.completed == []
        assert "第二次任务" in mgr.state.goal

    def test_update_unknown_tool(self):
        """未知工具名不报错，只记录 tools_used"""
        mgr = TaskStateManager()
        mgr.init_turn("test")
        mgr.update_from_tool("unknown_tool", {}, "some result")
        assert "unknown_tool" in mgr.state.tools_used
        assert len(mgr.state.completed) == 0
