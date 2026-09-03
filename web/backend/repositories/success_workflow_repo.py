"""Persistence for generalized successful workflow observations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novare.recovery.classifier import sanitize_error
from web.backend.db.models import SuccessfulWorkflowObservationModel

from .base import BaseRepository


class SuccessfulWorkflowRepository(BaseRepository):
    def __init__(self, db: AsyncSession, user_id: UUID):
        super().__init__(db, user_id)

    async def upsert_observation(
        self,
        trigger: dict,
        extracted: dict,
        *,
        model_name: str = "",
        environment_fingerprint: str = "",
        min_confidence: float = 0.7,
    ) -> SuccessfulWorkflowObservationModel:
        run_id = str(trigger.get("run_id") or "")[:32]
        result = await self.db.execute(
            select(SuccessfulWorkflowObservationModel).where(
                SuccessfulWorkflowObservationModel.user_id == self.user_id,
                SuccessfulWorkflowObservationModel.run_id == run_id,
            )
        )
        model = result.scalar_one_or_none()
        confidence = _score(extracted.get("confidence"))
        reusability = _score(extracted.get("reusability"))
        eligible = confidence >= min_confidence and reusability >= 0.7
        values = {
            "session_id": str(trigger.get("session_id") or "")[:64],
            "run_id": run_id,
            "turn_id": str(trigger.get("turn_id") or "")[:32],
            "task_signature": str(trigger.get("task_signature") or "")[:64],
            "workflow_key": str(extracted.get("workflow_key") or "")[:64],
            "workflow_family": sanitize_error(str(extracted.get("workflow_family") or ""))[:160],
            "workflow_name": sanitize_error(str(extracted.get("workflow_name") or ""))[:160],
            "summary": sanitize_error(str(extracted.get("summary") or ""))[:600],
            "when_to_use": sanitize_error(str(extracted.get("when_to_use") or ""))[:600],
            "prerequisites": _safe_strings(extracted.get("prerequisites"), 20, 300),
            "steps": _safe_steps(extracted.get("steps")),
            "decision_points": _safe_strings(extracted.get("decision_points"), 20, 400),
            "pitfalls": _safe_strings(extracted.get("pitfalls"), 20, 400),
            "verification_steps": _safe_strings(extracted.get("verification_steps"), 20, 400),
            "tool_sequence": _safe_tool_sequence(trigger.get("tool_sequence")),
            "existing_skill_match": (
                str(extracted.get("existing_skill_match"))[:80]
                if extracted.get("existing_skill_match") else None
            ),
            "reusability": reusability,
            "confidence": confidence,
            "complexity_score": _score(trigger.get("complexity_score")),
            "verification_status": str(trigger.get("verification_status") or "")[:40],
            "metrics": _safe_metrics(trigger.get("metrics")),
            "model_name": str(model_name or "")[:255],
            "environment_fingerprint": str(environment_fingerprint or "")[:64],
            "eligible_for_learning": eligible,
            "rejection_reason": "" if eligible else "confidence_or_reusability_below_threshold",
        }
        # Notice that trigger["user_goal"] is intentionally never persisted.
        if model is None:
            model = SuccessfulWorkflowObservationModel(user_id=self.user_id, **values)
            self.db.add(model)
        else:
            for key, value in values.items():
                setattr(model, key, value)
        await self.db.flush()
        return model

    async def list_observations(self, *, limit: int = 5000):
        result = await self.db.execute(
            select(SuccessfulWorkflowObservationModel)
            .where(SuccessfulWorkflowObservationModel.user_id == self.user_id)
            .order_by(SuccessfulWorkflowObservationModel.created_at.desc())
            .limit(max(1, min(5000, limit)))
        )
        return list(result.scalars().all())

    async def get_by_workflow_key(self, workflow_key: str, *, limit: int = 500):
        result = await self.db.execute(
            select(SuccessfulWorkflowObservationModel)
            .where(
                SuccessfulWorkflowObservationModel.user_id == self.user_id,
                SuccessfulWorkflowObservationModel.workflow_key == workflow_key,
            )
            .order_by(SuccessfulWorkflowObservationModel.created_at.desc())
            .limit(max(1, min(500, limit)))
        )
        return list(result.scalars().all())


def _score(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _safe_strings(value, max_items: int, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [sanitize_error(str(item))[:max_length] for item in value[:max_items]]


def _safe_steps(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:30]:
        if isinstance(item, dict):
            action = sanitize_error(str(item.get("action") or ""))[:500]
            if action:
                result.append({
                    "action": action,
                    "tool_hint": sanitize_error(str(item.get("tool_hint") or ""))[:128],
                })
    return result


def _safe_tool_sequence(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        names = item.get("argument_names") if isinstance(item.get("argument_names"), list) else []
        result.append({
            "tool": str(item.get("tool") or "")[:128],
            "argument_names": sorted(str(name)[:80] for name in names)[:30],
            "status": str(item.get("status") or "")[:30],
            "attempts": max(0, min(100, int(item.get("attempts") or 0))),
        })
    return result


def _safe_metrics(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        str(key)[:80]: item
        for key, item in list(value.items())[:30]
        if isinstance(item, (bool, int, float)) or item is None
    }
