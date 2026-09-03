"""Persistence and read models for observation-only self-evolution."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novare.evolution.types import make_lesson_key
from novare.recovery.classifier import sanitize_error
from web.backend.db.models import EvolutionExperienceModel, ReflectionResolutionModel

from .base import BaseRepository


class EvolutionObservationRepository(BaseRepository):
    def __init__(self, db: AsyncSession, user_id: UUID):
        super().__init__(db, user_id)

    async def upsert_observation(
        self,
        resolution: dict,
        *,
        model_name: str = "",
        environment_fingerprint: str = "",
        min_confidence: float = 0.6,
    ) -> tuple[ReflectionResolutionModel, EvolutionExperienceModel]:
        reflection_id = str(resolution.get("reflection_id") or "")[:64]
        existing_result = await self.db.execute(
            select(ReflectionResolutionModel).where(
                ReflectionResolutionModel.user_id == self.user_id,
                ReflectionResolutionModel.reflection_id == reflection_id,
            )
        )
        model = existing_result.scalar_one_or_none()
        resolved_at = _parse_datetime(resolution.get("resolved_at"))
        values = {
            "session_id": str(resolution.get("session_id") or "")[:64],
            "run_id": str(resolution.get("run_id") or "")[:32],
            "turn_id": str(resolution.get("turn_id") or "")[:32],
            "task_signature": str(resolution.get("task_signature") or "")[:64],
            "trigger": str(resolution.get("trigger") or "")[:64],
            "failure_type": str(resolution.get("failure_type") or "")[:80],
            "failed_tool": str(resolution.get("failed_tool") or "")[:128],
            "error_code": str(resolution.get("error_code") or "")[:80],
            "diagnosis": sanitize_error(str(resolution.get("diagnosis") or ""))[:300],
            "changes": _safe_strings(resolution.get("changes"), 20, 200),
            "revised_plan": _safe_strings(resolution.get("revised_plan"), 20, 200),
            "suggested_next_action": _safe_suggestion(resolution.get("suggested_next_action")),
            "status": str(resolution.get("status") or "uncertain")[:20],
            "confidence": _score(resolution.get("confidence")),
            "signals": {
                "suggested_action_executed": bool(resolution.get("suggested_action_executed")),
                "suggested_action_succeeded": bool(resolution.get("suggested_action_succeeded")),
                "suggested_action_failed": bool(resolution.get("suggested_action_failed")),
                "progress_after_reflection": bool(resolution.get("progress_after_reflection")),
                "repeated_failure_avoided": bool(resolution.get("repeated_failure_avoided")),
                "verification_passed": resolution.get("verification_passed"),
                "run_status": str(resolution.get("run_status") or "")[:20],
            },
            "evidence_event_ids": _safe_strings(resolution.get("evidence_event_ids"), 20, 100),
            "summary": sanitize_error(str(resolution.get("summary") or ""))[:300],
            "resolved_at": resolved_at,
        }
        if model is None:
            model = ReflectionResolutionModel(
                user_id=self.user_id,
                reflection_id=reflection_id,
                **values,
            )
            self.db.add(model)
            await self.db.flush()
        else:
            for key, value in values.items():
                setattr(model, key, value)
            await self.db.flush()

        lesson = "; ".join(values["changes"]) or values["diagnosis"]
        lesson = sanitize_error(lesson)[:600] or "未形成可泛化经验"
        lesson_key = make_lesson_key(
            trigger=values["trigger"],
            failure_type=values["failure_type"],
            failed_tool=values["failed_tool"],
            lesson=lesson,
        )
        exp_result = await self.db.execute(
            select(EvolutionExperienceModel).where(
                EvolutionExperienceModel.user_id == self.user_id,
                EvolutionExperienceModel.reflection_id == reflection_id,
            )
        )
        experience = exp_result.scalar_one_or_none()
        eligible = values["status"] == "helpful" and values["confidence"] >= min_confidence
        exp_values = {
            "reflection_resolution_id": model.id,
            "session_id": values["session_id"],
            "run_id": values["run_id"],
            "task_signature": values["task_signature"],
            "lesson_key": lesson_key,
            "experience_type": "failure_lesson",
            "trigger": values["trigger"],
            "failure_type": values["failure_type"],
            "failed_tool": values["failed_tool"],
            "error_code": values["error_code"],
            "generalized_lesson": lesson,
            "resolution_status": values["status"],
            "resolution_confidence": values["confidence"],
            "evidence_refs": [f"reflection:{reflection_id}"] + [
                f"event:{event_id}" for event_id in values["evidence_event_ids"]
            ],
            "model_name": str(model_name or "")[:255],
            "environment_fingerprint": str(environment_fingerprint or "")[:64],
            "eligible_for_learning": eligible,
            "rejection_reason": "" if eligible else _rejection_reason(values),
        }
        if experience is None:
            experience = EvolutionExperienceModel(
                user_id=self.user_id,
                reflection_id=reflection_id,
                **exp_values,
            )
            self.db.add(experience)
        else:
            for key, value in exp_values.items():
                setattr(experience, key, value)
        await self.db.flush()
        return model, experience

    async def list_resolutions(self, *, limit: int = 100) -> list[ReflectionResolutionModel]:
        result = await self.db.execute(
            select(ReflectionResolutionModel)
            .where(ReflectionResolutionModel.user_id == self.user_id)
            .order_by(ReflectionResolutionModel.created_at.desc())
            .limit(max(1, min(500, limit)))
        )
        return list(result.scalars().all())

    async def list_experiences(self, *, limit: int = 5000) -> list[EvolutionExperienceModel]:
        result = await self.db.execute(
            select(EvolutionExperienceModel)
            .where(EvolutionExperienceModel.user_id == self.user_id)
            .order_by(EvolutionExperienceModel.created_at.desc())
            .limit(max(1, min(5000, limit)))
        )
        return list(result.scalars().all())

    async def get_experiences_by_lesson_key(
        self, lesson_key: str, *, limit: int = 500,
    ) -> list[EvolutionExperienceModel]:
        result = await self.db.execute(
            select(EvolutionExperienceModel)
            .where(
                EvolutionExperienceModel.user_id == self.user_id,
                EvolutionExperienceModel.lesson_key == lesson_key,
            )
            .order_by(EvolutionExperienceModel.created_at.desc())
            .limit(max(1, min(500, limit)))
        )
        return list(result.scalars().all())


def _safe_strings(value, max_items: int, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [sanitize_error(str(item))[:max_length] for item in value[:max_items]]


def _safe_suggestion(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    tool = value.get("tool")
    arguments = value.get("arguments")
    argument_names = value.get("argument_names")
    if not isinstance(tool, str):
        return None
    # Arguments can contain secrets; retain only their names for observation.
    if isinstance(arguments, dict):
        names = arguments.keys()
    elif isinstance(argument_names, list):
        names = argument_names
    else:
        names = []
    return {"tool": tool[:128], "argument_names": sorted(str(k)[:80] for k in names)[:30]}


def _score(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _rejection_reason(values: dict) -> str:
    if values["status"] != "helpful":
        return f"resolution_status={values['status']}"
    return "confidence_below_threshold"
