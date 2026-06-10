"""novare/subagents/manifest.py — 文件级子智能体清单（调试/审计用）

借鉴 claw-code 的文件清单模式，但简化为写入式日志：
每个子智能体的状态变更写入 workspace/.novare/subagents/{id}.json。
仅用于调试和事后分析，不参与运行时通信。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novare.subagents.types import SubagentRecord

logger = logging.getLogger("novare.subagents.manifest")


def _manifest_dir(workspace: Path) -> Path:
    return workspace / ".novare" / "subagents"


def write_manifest(workspace: Path, record: SubagentRecord) -> None:
    """写入子智能体清单到磁盘

    在状态转换时调用（RUNNING, COMPLETED, FAILED, CANCELLED）。
    """
    d = _manifest_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)

    path = d / f"{record.subagent_id}.json"
    data = {
        "subagent_id": record.subagent_id,
        "type": record.type.value,
        "task": record.task,
        "status": record.status.value,
        "result_preview": record.result[:500] if record.result else "",
        "tool_calls_made": record.tool_calls_made,
        "created_at": record.created_at,
        "finished_at": record.finished_at,
        "elapsed_seconds": round(record.elapsed, 2),
        "error": record.error,
    }

    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to write manifest %s: %s", path, e)


def read_manifest(workspace: Path, subagent_id: str) -> dict | None:
    """读取子智能体清单"""
    path = _manifest_dir(workspace) / f"{subagent_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read manifest %s: %s", path, e)
        return None


def list_manifests(workspace: Path) -> list[dict]:
    """列出所有清单"""
    d = _manifest_dir(workspace)
    if not d.exists():
        return []

    result = []
    for p in sorted(d.glob("sa-*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return result
