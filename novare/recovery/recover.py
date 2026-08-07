"""novare/recovery/recover.py — 生产恢复与对账

在 Web session 开始新 turn 前调用 recover_incomplete_runs()，
修复中断的 run，确保协议完整性。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from novare.recovery.state import (
    RecoveryState,
    ToolCallStatus,
    _make_synthetic_result,
)

if TYPE_CHECKING:
    from novare.session import Session

logger = logging.getLogger("novare.recovery.recover")


async def recover_incomplete_runs(
    session: Session,
    recovery_state: RecoveryState,
    commit_fn=None,
) -> str | None:
    """恢复中断的 run，修复不完整的 tool call。

    规则：
    - 有 TOOL_COMPLETED 事件但缺 tool 消息：投影保存的 result，不重执行
    - 只有 TOOL_STARTED：
      - non_idempotent：写 UNKNOWN_OUTCOME，不重放
      - read/idempotent_write：可以使用原 key 安全重试，或写 INTERRUPTED
    - 已注册但未 started：写 INTERRUPTED/SKIPPED，不执行
    - 已有 tool result：不得重复写
    - 恢复函数本身必须幂等

    Args:
        session: 会话对象
        recovery_state: RecoveryState（从持久化恢复）
        commit_fn: 可选的提交函数

    Returns:
        恢复状态描述（"recovered" / "interrupted" / None 如果无需恢复）
    """
    incomplete = recovery_state.check_completeness()
    if not incomplete:
        # 检查 run_status 是否需要更新
        status_val = recovery_state.run_status.value if hasattr(recovery_state.run_status, 'value') else recovery_state.run_status
        if status_val == "running":
            recovery_state.set_run_status("recovered")
        return None

    recovered_count = 0
    for tc_id in incomplete:
        record = recovery_state.get_record(tc_id)
        if not record:
            continue

        # 检查 session 中是否已有该 tool result
        has_result = any(
            m.get("role") == "tool" and m.get("tool_call_id") == tc_id
            for m in session.messages
        )
        if has_result:
            recovery_state.committed_tool_result_ids.add(tc_id)
            continue

        # 根据工具状态决定恢复策略
        if record.status == ToolCallStatus.COMPLETED:
            # 有完成记录但缺消息 → 需要投影（但这里没有 result 内容）
            # 标记为中断，由上层处理
            synthetic = _make_synthetic_result(
                tc_id, record.tool_name, ToolCallStatus.INTERRUPTED,
                "Completed but result missing",
            )
            await _commit_synthetic(session, recovery_state, tc_id, synthetic, commit_fn)
            recovered_count += 1

        elif record.status == ToolCallStatus.EXECUTING:
            # 已开始执行但未完成
            if record.idempotency == "non_idempotent":
                # 非幂等工具已开始，不知道是否完成
                synthetic = _make_synthetic_result(
                    tc_id, record.tool_name, ToolCallStatus.UNKNOWN_OUTCOME,
                    "Execution started but outcome unknown",
                )
            else:
                # 只读/幂等写入可以安全重试，但先标记为中断
                synthetic = _make_synthetic_result(
                    tc_id, record.tool_name, ToolCallStatus.INTERRUPTED,
                    "Execution interrupted",
                )
            await _commit_synthetic(session, recovery_state, tc_id, synthetic, commit_fn)
            recovered_count += 1

        elif record.status == ToolCallStatus.PENDING:
            # 已注册但未 started
            synthetic = _make_synthetic_result(
                tc_id, record.tool_name, ToolCallStatus.SKIPPED,
                "Never started",
            )
            await _commit_synthetic(session, recovery_state, tc_id, synthetic, commit_fn)
            recovered_count += 1

        else:
            # 其他状态（FAILED/terminal）→ 标记为中断
            synthetic = _make_synthetic_result(
                tc_id, record.tool_name, ToolCallStatus.INTERRUPTED,
                f"Recovery: was {record.status.value}",
            )
            await _commit_synthetic(session, recovery_state, tc_id, synthetic, commit_fn)
            recovered_count += 1

    # 更新 run status
    if recovered_count > 0:
        recovery_state.set_run_status("recovered")
        logger.info(
            "Recovered %d incomplete tool calls for run %s",
            recovered_count, recovery_state.run_id,
        )
        return "recovered"

    return None


async def _commit_synthetic(
    session: Session,
    recovery_state: RecoveryState,
    tool_call_id: str,
    result: str,
    commit_fn=None,
) -> None:
    """提交合成结果"""
    if commit_fn:
        await commit_fn(session, recovery_state, tool_call_id, result)
    else:
        session.add_tool_result(tool_call_id, result)
    recovery_state.committed_tool_result_ids.add(tool_call_id)
