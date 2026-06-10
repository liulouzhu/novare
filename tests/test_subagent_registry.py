"""tests/test_subagent_registry.py — SubagentRegistry 生命周期测试"""

import asyncio
import pytest

from novare.subagents.registry import SubagentRegistry
from novare.subagents.types import SubagentType, SubagentStatus


class TestSubagentRegistry:
    def test_create_generates_unique_ids(self):
        registry = SubagentRegistry()
        r1 = registry.create(SubagentType.SEARCH, "task 1")
        r2 = registry.create(SubagentType.ANALYZER, "task 2")

        assert r1.subagent_id != r2.subagent_id
        assert r1.subagent_id.startswith("sa-")
        assert r2.subagent_id.startswith("sa-")
        assert r1.status == SubagentStatus.PENDING
        assert r2.status == SubagentStatus.PENDING

    def test_get_existing_record(self):
        registry = SubagentRegistry()
        created = registry.create(SubagentType.SEARCH, "test task")
        found = registry.get(created.subagent_id)

        assert found is not None
        assert found.subagent_id == created.subagent_id
        assert found.task == "test task"

    def test_get_nonexistent_returns_none(self):
        registry = SubagentRegistry()
        assert registry.get("sa-nonexistent") is None

    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")

        async def dummy_coro():
            return "done"

        await registry.start(record.subagent_id, dummy_coro())
        assert record.status == SubagentStatus.RUNNING
        assert record.asyncio_task is not None

    @pytest.mark.asyncio
    async def test_start_nonexistent_raises(self):
        registry = SubagentRegistry()
        with pytest.raises(KeyError):
            await registry.start("sa-nonexistent", asyncio.sleep(0))

    def test_complete_transitions_to_completed(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")

        registry.complete(record.subagent_id, "result text")
        assert record.status == SubagentStatus.COMPLETED
        assert record.result == "result text"
        assert record.finished_at is not None

    def test_fail_transitions_to_failed(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")

        registry.fail(record.subagent_id, "something went wrong")
        assert record.status == SubagentStatus.FAILED
        assert record.error == "something went wrong"
        assert record.finished_at is not None

    def test_complete_nonexistent_is_noop(self):
        registry = SubagentRegistry()
        registry.complete("sa-nonexistent", "result")  # Should not raise

    def test_fail_nonexistent_is_noop(self):
        registry = SubagentRegistry()
        registry.fail("sa-nonexistent", "error")  # Should not raise

    @pytest.mark.asyncio
    async def test_cancel_running_task(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")

        async def long_coro():
            await asyncio.sleep(100)
            return "done"

        await registry.start(record.subagent_id, long_coro())
        assert record.status == SubagentStatus.RUNNING

        result = await registry.cancel(record.subagent_id)
        assert result is True
        assert record.status == SubagentStatus.CANCELLED
        assert record.finished_at is not None

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self):
        registry = SubagentRegistry()
        result = await registry.cancel("sa-nonexistent")
        assert result is False

    def test_get_output(self):
        registry = SubagentRegistry()
        record = registry.create(SubagentType.SEARCH, "test")
        registry.complete(record.subagent_id, "found 5 papers")

        output = registry.get_output(record.subagent_id)
        assert output is not None
        assert output.subagent_id == record.subagent_id
        assert output.status == SubagentStatus.COMPLETED
        assert output.result == "found 5 papers"

    def test_get_output_nonexistent_returns_none(self):
        registry = SubagentRegistry()
        assert registry.get_output("sa-nonexistent") is None

    def test_list_active_filters_correctly(self):
        registry = SubagentRegistry()
        r1 = registry.create(SubagentType.SEARCH, "pending task")
        r2 = registry.create(SubagentType.ANALYZER, "will complete")
        r3 = registry.create(SubagentType.WRITER, "will fail")

        registry.complete(r2.subagent_id, "done")
        registry.fail(r3.subagent_id, "error")

        active = registry.list_active()
        assert len(active) == 1
        assert active[0].subagent_id == r1.subagent_id

    def test_list_all_returns_everything(self):
        registry = SubagentRegistry()
        registry.create(SubagentType.SEARCH, "task 1")
        registry.create(SubagentType.ANALYZER, "task 2")

        all_records = registry.list_all()
        assert len(all_records) == 2

    def test_cleanup_finished_removes_old_records(self):
        import time
        registry = SubagentRegistry()
        r1 = registry.create(SubagentType.SEARCH, "old task")
        r2 = registry.create(SubagentType.SEARCH, "recent task")

        registry.complete(r1.subagent_id, "done")
        registry.complete(r2.subagent_id, "done")

        # Simulate old record
        r1.finished_at = time.monotonic() - 7200  # 2 hours ago

        removed = registry.cleanup_finished(max_age_seconds=3600)
        assert removed == 1
        assert registry.get(r1.subagent_id) is None
        assert registry.get(r2.subagent_id) is not None

    @pytest.mark.asyncio
    async def test_cancel_all(self):
        registry = SubagentRegistry()
        r1 = registry.create(SubagentType.SEARCH, "task 1")
        r2 = registry.create(SubagentType.ANALYZER, "task 2")
        r3 = registry.create(SubagentType.WRITER, "task 3")

        async def long_coro():
            await asyncio.sleep(100)

        await registry.start(r1.subagent_id, long_coro())
        await registry.start(r2.subagent_id, long_coro())
        registry.complete(r3.subagent_id, "already done")

        cancelled = await registry.cancel_all()
        assert cancelled == 2  # Only r1 and r2 were active
        assert r1.status == SubagentStatus.CANCELLED
        assert r2.status == SubagentStatus.CANCELLED
        assert r3.status == SubagentStatus.COMPLETED  # Unchanged
