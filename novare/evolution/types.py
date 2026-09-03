"""Structured types for the observation stage of self-evolution."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ResolutionStatus(str, Enum):
    """Observed effect of one committed reflection."""

    PENDING = "pending"
    HELPFUL = "helpful"
    INEFFECTIVE = "ineffective"
    HARMFUL = "harmful"
    UNCERTAIN = "uncertain"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_task_signature(goal: str) -> str:
    """Return a privacy-preserving signature for cross-run task grouping."""
    normalized = re.sub(r"\s+", " ", (goal or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_lesson_key(
    *, trigger: str, failure_type: str, failed_tool: str, lesson: str,
) -> str:
    """Build a stable grouping key without retaining additional raw data."""
    normalized_lesson = re.sub(r"\s+", " ", (lesson or "").strip().lower())
    payload = {
        "trigger": trigger or "",
        "failure_type": failure_type or "",
        "failed_tool": failed_tool or "",
        "lesson": normalized_lesson,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ReflectionResolution:
    """Outcome evidence collected after a reflection was committed.

    The original ReflectionRecord remains immutable.  This object is a
    separate, auditable observation and never authorizes a skill mutation.
    """

    reflection_id: str
    session_id: str
    run_id: str
    turn_id: str
    task_signature: str
    trigger: str
    failure_type: str
    diagnosis: str
    changes: list[str]
    revised_plan: list[str]
    suggested_next_action: dict | None
    failed_tool: str = ""
    error_code: str = ""
    status: ResolutionStatus = ResolutionStatus.PENDING
    confidence: float = 0.0
    committed_iteration: int = 0
    resolved_iteration: int | None = None
    suggested_action_executed: bool = False
    suggested_action_succeeded: bool = False
    suggested_action_failed: bool = False
    progress_after_reflection: bool = False
    repeated_failure_avoided: bool = False
    verification_passed: bool | None = None
    run_status: str = "running"
    evidence_event_ids: list[str] = field(default_factory=list)
    summary: str = ""
    resolution_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_now_iso)
    resolved_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "resolution_id": self.resolution_id,
            "reflection_id": self.reflection_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "task_signature": self.task_signature,
            "trigger": self.trigger,
            "failure_type": self.failure_type,
            "diagnosis": self.diagnosis,
            "changes": list(self.changes),
            "revised_plan": list(self.revised_plan),
            "suggested_next_action": self.suggested_next_action,
            "failed_tool": self.failed_tool,
            "error_code": self.error_code,
            "status": self.status.value,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "committed_iteration": self.committed_iteration,
            "resolved_iteration": self.resolved_iteration,
            "suggested_action_executed": self.suggested_action_executed,
            "suggested_action_succeeded": self.suggested_action_succeeded,
            "suggested_action_failed": self.suggested_action_failed,
            "progress_after_reflection": self.progress_after_reflection,
            "repeated_failure_avoided": self.repeated_failure_avoided,
            "verification_passed": self.verification_passed,
            "run_status": self.run_status,
            "evidence_event_ids": list(self.evidence_event_ids),
            "summary": self.summary,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            # Observation mode invariant: this record is evidence, not a write proposal.
            "applied": False,
        }
