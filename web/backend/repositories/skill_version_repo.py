"""Skill version lineage and exact-version execution attribution."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from novare.evolution.skill_proposals import content_sha256
from web.backend.db.models import SkillExecutionModel, SkillVersionModel

from .base import BaseRepository


class SkillVersionRepository(BaseRepository):
    def __init__(self, db: AsyncSession, user_id: UUID):
        super().__init__(db, user_id)

    async def ensure_version(
        self,
        *,
        skill_name: str,
        content: str,
        source_kind: str,
        source_path: str = "",
        proposal_id: UUID | None = None,
        activate: bool = False,
    ) -> SkillVersionModel:
        digest = content_sha256(content)
        result = await self.db.execute(
            select(SkillVersionModel).where(
                SkillVersionModel.user_id == self.user_id,
                SkillVersionModel.skill_name == skill_name,
                SkillVersionModel.content_sha256 == digest,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            active_result = await self.db.execute(
                select(SkillVersionModel).where(
                    SkillVersionModel.user_id == self.user_id,
                    SkillVersionModel.skill_name == skill_name,
                    SkillVersionModel.is_active.is_(True),
                )
            )
            parent = active_result.scalar_one_or_none()
            max_version = await self.db.scalar(
                select(func.max(SkillVersionModel.version)).where(
                    SkillVersionModel.user_id == self.user_id,
                    SkillVersionModel.skill_name == skill_name,
                )
            )
            version = SkillVersionModel(
                user_id=self.user_id,
                skill_name=skill_name[:80],
                version=int(max_version or 0) + 1,
                content_sha256=digest,
                content=content,
                source_kind=source_kind,
                source_path=source_path,
                proposal_id=proposal_id,
                parent_version_id=parent.id if parent else None,
                is_active=False,
            )
            self.db.add(version)
            await self.db.flush()
        if activate:
            await self.db.execute(
                update(SkillVersionModel)
                .where(
                    SkillVersionModel.user_id == self.user_id,
                    SkillVersionModel.skill_name == skill_name,
                    SkillVersionModel.id != version.id,
                )
                .values(is_active=False)
            )
            version.is_active = True
            await self.db.flush()
        return version

    async def get(self, version_id: UUID) -> SkillVersionModel | None:
        result = await self.db.execute(
            select(SkillVersionModel).where(
                SkillVersionModel.id == version_id,
                SkillVersionModel.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_versions(self, skill_name: str) -> list[SkillVersionModel]:
        result = await self.db.execute(
            select(SkillVersionModel)
            .where(
                SkillVersionModel.user_id == self.user_id,
                SkillVersionModel.skill_name == skill_name,
            )
            .order_by(SkillVersionModel.version.desc())
        )
        return list(result.scalars().all())

    async def deactivate_skill(self, skill_name: str) -> None:
        """Mark every version inactive when a newly-created Skill is rolled back."""
        await self.db.execute(
            update(SkillVersionModel)
            .where(
                SkillVersionModel.user_id == self.user_id,
                SkillVersionModel.skill_name == skill_name,
            )
            .values(is_active=False)
        )
        await self.db.flush()

    async def record_execution(
        self,
        *,
        version_id: UUID,
        session_id: str | None,
        run_id: str,
        turn_id: str,
        selection_mode: str = "explicit",
        outcome: str,
        score: float,
        verification_status: str,
        run_status: str,
        metrics: dict,
    ) -> SkillExecutionModel:
        version = await self.get(version_id)
        if version is None:
            raise ValueError("Skill version does not belong to current user")
        execution = SkillExecutionModel(
            user_id=self.user_id,
            session_id=session_id,
            run_id=run_id[:32],
            turn_id=turn_id[:32],
            skill_version_id=version.id,
            skill_name=version.skill_name,
            content_sha256=version.content_sha256,
            selection_mode=(
                "automatic" if selection_mode == "automatic" else "explicit"
            ),
            outcome=outcome,
            score=max(0.0, min(1.0, float(score))),
            verification_status=verification_status[:40],
            run_status=run_status[:20],
            metrics=_safe_metrics(metrics),
        )
        self.db.add(execution)
        await self.db.flush()
        return execution

    async def list_executions(
        self, *, skill_name: str | None = None, limit: int = 200,
    ) -> list[SkillExecutionModel]:
        query = select(SkillExecutionModel).where(
            SkillExecutionModel.user_id == self.user_id,
        )
        if skill_name:
            query = query.where(SkillExecutionModel.skill_name == skill_name)
        result = await self.db.execute(
            query.order_by(SkillExecutionModel.created_at.desc())
            .limit(max(1, min(1000, limit)))
        )
        return list(result.scalars().all())


def _safe_metrics(metrics: dict) -> dict:
    if not isinstance(metrics, dict):
        return {}
    safe = {}
    for key, value in list(metrics.items())[:30]:
        if isinstance(value, (bool, int, float)) or value is None:
            safe[str(key)[:80]] = value
        elif isinstance(value, str):
            safe[str(key)[:80]] = value[:300]
    return safe
