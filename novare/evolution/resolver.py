"""Deterministic attribution of post-reflection execution outcomes."""

from __future__ import annotations

from datetime import datetime, timezone

from novare.reflexion.triggers import ToolEventSummary, compute_action_fingerprint
from novare.reflexion.types import ReflectionRecord

from .types import ReflectionResolution, ResolutionStatus, make_task_signature


_VERIFICATION_PASS = {"verified", "revised", "verified_with_risk"}
_VERIFICATION_FAIL = {"repair_failed", "failed"}
_COMPLETED_RUN_STATUSES = {"completed", "done"}


class ReflectionResolutionTracker:
    """Track evidence occurring strictly after each committed reflection."""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        turn_id: str,
        user_goal: str,
    ) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.turn_id = turn_id
        self.task_signature = make_task_signature(user_goal)
        self._items: list[tuple[ReflectionResolution, str | None, set[str]]] = []
        self._finalized = False

    def register(
        self,
        record: ReflectionRecord,
        *,
        iteration: int,
        failed_tool: str | None = None,
        error_code: str | None = None,
    ) -> None:
        suggested_fp: str | None = None
        suggested = record.suggested_next_action
        if isinstance(suggested, dict):
            tool = suggested.get("tool")
            arguments = suggested.get("arguments")
            if isinstance(tool, str) and tool and isinstance(arguments, dict):
                suggested_fp = compute_action_fingerprint(tool, arguments)

        resolution = ReflectionResolution(
            reflection_id=record.reflection_id,
            session_id=self.session_id,
            run_id=self.run_id,
            turn_id=self.turn_id,
            task_signature=self.task_signature,
            trigger=record.trigger,
            failure_type=record.failure_type,
            diagnosis=record.diagnosis,
            changes=list(record.changes),
            revised_plan=list(record.revised_plan),
            suggested_next_action=record.suggested_next_action,
            failed_tool=failed_tool or "",
            error_code=error_code or "",
            committed_iteration=iteration,
        )
        self._items.append(
            (resolution, suggested_fp, set(record.forbidden_action_fingerprints))
        )

    def observe_batch(
        self,
        *,
        iteration: int,
        events: list[ToolEventSummary],
        made_progress: bool,
    ) -> None:
        """Attach only later-batch evidence; the trigger batch cannot self-validate."""
        for resolution, suggested_fp, forbidden in self._items:
            if iteration <= resolution.committed_iteration:
                continue
            successful_events = [event for event in events if event.ok]
            if made_progress and successful_events:
                resolution.progress_after_reflection = True
                resolution.resolved_iteration = iteration
            for event in events:
                if event.tool_call_id not in resolution.evidence_event_ids:
                    resolution.evidence_event_ids.append(event.tool_call_id)
                    resolution.evidence_event_ids = resolution.evidence_event_ids[-20:]
                if suggested_fp and event.action_fingerprint == suggested_fp:
                    resolution.suggested_action_executed = True
                    if event.ok:
                        resolution.suggested_action_succeeded = True
                        resolution.resolved_iteration = iteration
                    else:
                        resolution.suggested_action_failed = True
                        resolution.resolved_iteration = iteration
                if event.ok and event.action_fingerprint not in forbidden:
                    resolution.repeated_failure_avoided = True

    @staticmethod
    def _verification_signal(verification: dict | None) -> bool | None:
        if not isinstance(verification, dict):
            return None
        status = str(verification.get("status") or "").lower()
        if status in _VERIFICATION_PASS:
            return True
        if status in _VERIFICATION_FAIL:
            return False
        return None

    def finalize(
        self,
        *,
        run_status: str,
        verification: dict | None,
    ) -> list[ReflectionResolution]:
        if self._finalized:
            return []
        self._finalized = True
        verified = self._verification_signal(verification)
        now = datetime.now(timezone.utc).isoformat()

        for resolution, _suggested_fp, _forbidden in self._items:
            resolution.run_status = run_status
            resolution.verification_passed = verified
            score = 0.0
            if resolution.suggested_action_succeeded:
                score += 0.45
            if resolution.progress_after_reflection:
                score += 0.15
            if resolution.repeated_failure_avoided:
                score += 0.10
            if verified is True:
                score += 0.35
            if resolution.suggested_action_failed:
                score -= 0.45
            if verified is False:
                score -= 0.35
            if run_status not in _COMPLETED_RUN_STATUSES:
                score -= 0.25

            if score >= 0.60:
                resolution.status = ResolutionStatus.HELPFUL
            elif score <= -0.60:
                resolution.status = ResolutionStatus.HARMFUL
            elif score <= -0.30:
                resolution.status = ResolutionStatus.INEFFECTIVE
            else:
                resolution.status = ResolutionStatus.UNCERTAIN
            resolution.confidence = min(1.0, abs(score))
            resolution.resolved_at = now
            resolution.summary = self._summary(resolution)
        return [item[0] for item in self._items]

    @staticmethod
    def _summary(resolution: ReflectionResolution) -> str:
        signals: list[str] = []
        if resolution.suggested_action_succeeded:
            signals.append("建议动作执行成功")
        elif resolution.suggested_action_failed:
            signals.append("建议动作执行失败")
        if resolution.progress_after_reflection:
            signals.append("反思后出现新进展")
        if resolution.verification_passed is True:
            signals.append("最终回答通过证据核验")
        elif resolution.verification_passed is False:
            signals.append("最终回答未通过证据核验")
        if not signals:
            signals.append("缺少可归因的后续证据")
        return "；".join(signals)[:300]
