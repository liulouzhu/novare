"""tests/test_subagent_manifest.py — manifest 文件持久化测试"""

import json
import pytest
from pathlib import Path

from novare.subagents.manifest import write_manifest, read_manifest, list_manifests
from novare.subagents.types import SubagentRecord, SubagentType, SubagentStatus


class TestManifest:
    def test_write_and_read_manifest(self, tmp_path: Path):
        record = SubagentRecord(
            subagent_id="sa-test123",
            type=SubagentType.SEARCH,
            task="搜索 Transformer 论文",
            status=SubagentStatus.COMPLETED,
            result="找到了 10 篇相关论文",
            tool_calls_made=3,
        )
        record.finished_at = record.created_at + 15.0

        write_manifest(tmp_path, record)

        data = read_manifest(tmp_path, "sa-test123")
        assert data is not None
        assert data["subagent_id"] == "sa-test123"
        assert data["type"] == "search"
        assert data["status"] == "completed"
        assert data["tool_calls_made"] == 3
        assert "找到了" in data["result_preview"]

    def test_read_nonexistent_manifest(self, tmp_path: Path):
        result = read_manifest(tmp_path, "sa-nonexistent")
        assert result is None

    def test_list_manifests(self, tmp_path: Path):
        # Write two manifests
        for i, sid in enumerate(["sa-aaa", "sa-bbb"]):
            record = SubagentRecord(
                subagent_id=sid,
                type=SubagentType.SEARCH,
                task=f"task {i}",
                status=SubagentStatus.COMPLETED,
            )
            write_manifest(tmp_path, record)

        manifests = list_manifests(tmp_path)
        assert len(manifests) == 2
        ids = {m["subagent_id"] for m in manifests}
        assert ids == {"sa-aaa", "sa-bbb"}

    def test_list_manifests_empty_dir(self, tmp_path: Path):
        manifests = list_manifests(tmp_path)
        assert manifests == []

    def test_write_creates_directory(self, tmp_path: Path):
        workspace = tmp_path / "new_workspace"
        record = SubagentRecord(
            subagent_id="sa-xxx",
            type=SubagentType.EXPLORER,
            task="test",
        )
        write_manifest(workspace, record)

        manifest_path = workspace / ".novare" / "subagents" / "sa-xxx.json"
        assert manifest_path.exists()

    def test_manifest_json_format(self, tmp_path: Path):
        record = SubagentRecord(
            subagent_id="sa-format",
            type=SubagentType.ANALYZER,
            task="test task",
            status=SubagentStatus.FAILED,
            error="connection timeout",
        )

        write_manifest(tmp_path, record)

        path = tmp_path / ".novare" / "subagents" / "sa-format.json"
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)

        # Should be valid JSON with expected keys
        assert data["subagent_id"] == "sa-format"
        assert data["type"] == "analyzer"
        assert data["status"] == "failed"
        assert data["error"] == "connection timeout"
        assert "elapsed_seconds" in data
        assert "created_at" in data
