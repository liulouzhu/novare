"""统一记忆提取协调层 — 一次 LLM 调用，分别存储画像和情景记忆。

流程：
1. 读取现有画像上下文（通过 MemoryServiceAsync.get_extraction_context）
2. 统一调用一次 LLM（通过 UnifiedMemoryExtractor）
3. 分别保存画像和情景记忆（通过各自 Service 的 save_extracted）

两个存储服务保持失败隔离：一个失败不影响另一个。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novare.llm_client import LLMClient
    from web.backend.memory_service import MemoryServiceAsync
    from web.backend.episodic_memory.service import EpisodicMemoryService

from .extractor import UnifiedMemoryExtractor

logger = logging.getLogger("novare.memory_extraction.coordinator")


class MemoryExtractionCoordinator:
    """统一记忆提取协调器。"""

    def __init__(
        self,
        memory_service: MemoryServiceAsync | None = None,
        episodic_memory_service: EpisodicMemoryService | None = None,
    ):
        self._memory_service = memory_service
        self._episodic_memory_service = episodic_memory_service
        self._extractor = UnifiedMemoryExtractor()

    async def extract_and_persist(
        self,
        *,
        user_id: str,
        session_id: str,
        messages: list[dict],
        llm_client: LLMClient,
    ) -> dict:
        """统一提取并持久化记忆。

        Returns:
            内部结果字典，仅用于日志/测试：
            {
                "profile_saved": int,
                "episodes_saved": int,
                "episodes_indexed": int,
                "episodes_index_failed": int,
                "warnings": list[str]
            }
        """
        result: dict = {
            "profile_saved": 0,
            "episodes_saved": 0,
            "episodes_indexed": 0,
            "episodes_index_failed": 0,
            "warnings": [],
        }

        extract_profile = self._memory_service is not None
        extract_episodes = (
            self._episodic_memory_service is not None
            and self._episodic_memory_service.enabled
        )

        # 两者都关闭时直接返回
        if not extract_profile and not extract_episodes:
            return result

        # 读取现有画像上下文
        existing_profile = ""
        if extract_profile:
            try:
                existing_profile = await self._memory_service.get_extraction_context(user_id)
            except Exception:
                logger.debug("Failed to get extraction context (non-fatal)")

        # 计算 max_episodes：从 episodic_memory_service.max_per_turn 获取
        max_episodes = 3
        if extract_episodes:
            max_episodes = self._episodic_memory_service.max_per_turn

        # ── 唯一一次 LLM 调用 ──
        try:
            extraction = await self._extractor.extract(
                messages=messages,
                llm_client=llm_client,
                existing_profile=existing_profile,
                extract_profile=extract_profile,
                extract_episodes=extract_episodes,
                max_episodes=max_episodes,
            )
        except Exception:
            logger.exception("Unified memory extraction failed for user %s", user_id)
            result["warnings"].append("extraction_failed")
            return result

        # ── 分别持久化，失败隔离 ──
        async def _save_profile():
            nonlocal result
            if not extract_profile or not extraction.profile_updates:
                return
            try:
                saved = await self._memory_service.save_extracted(
                    user_id=user_id,
                    candidates=extraction.profile_updates,
                )
                result["profile_saved"] = len(saved)
            except Exception:
                logger.exception("Profile persistence failed for user %s", user_id)
                if "profile_persist_failed" not in result["warnings"]:
                    result["warnings"].append("profile_persist_failed")

        async def _save_episodes():
            nonlocal result
            if not extract_episodes or not extraction.episodes:
                return
            try:
                saved = await self._episodic_memory_service.save_extracted(
                    user_id=user_id,
                    session_id=session_id,
                    candidates=extraction.episodes,
                )
                result["episodes_saved"] = len(saved)
                # 统计 index_status
                for rec in saved:
                    status = rec.get("index_status", "")
                    if status == "indexed":
                        result["episodes_indexed"] += 1
                    elif status == "failed":
                        result["episodes_index_failed"] += 1
                if result["episodes_index_failed"] > 0:
                    if "episodic_index_failed" not in result["warnings"]:
                        result["warnings"].append("episodic_index_failed")
            except Exception:
                logger.exception("Episodic persistence failed for user %s", user_id)
                if "episodic_persist_failed" not in result["warnings"]:
                    result["warnings"].append("episodic_persist_failed")

        # 两个存储服务失败隔离
        profile_result, episode_result = await asyncio.gather(
            _save_profile(),
            _save_episodes(),
            return_exceptions=True,
        )

        if isinstance(profile_result, Exception):
            logger.exception("Profile persistence raised for user %s", user_id)
            if "profile_persist_failed" not in result["warnings"]:
                result["warnings"].append("profile_persist_failed")
        if isinstance(episode_result, Exception):
            logger.exception("Episodic persistence raised for user %s", user_id)
            if "episodic_persist_failed" not in result["warnings"]:
                result["warnings"].append("episodic_persist_failed")

        # 结构化日志（不记录完整候选、对话或已有画像）
        if result["warnings"]:
            logger.warning(
                "Memory extraction completed with warnings for user %s: %s",
                user_id,
                "; ".join(result["warnings"]),
            )
        logger.info(
            "Memory extraction completed for user %s: profile_saved=%d, episodes_saved=%d",
            user_id,
            result["profile_saved"],
            result["episodes_saved"],
        )

        return result
