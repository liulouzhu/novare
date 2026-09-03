"""Deterministic candidate-report aggregation for observation mode."""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean


def build_candidate_reports(
    experiences: list[dict], *, min_independent_sessions: int = 3,
) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for experience in experiences:
        key = str(experience.get("lesson_key") or "")
        if key:
            grouped[key].append(experience)

    reports: list[dict] = []
    for lesson_key, items in grouped.items():
        sessions = {str(item.get("session_id") or "") for item in items}
        sessions.discard("")
        counts = defaultdict(int)
        for item in items:
            counts[str(item.get("resolution_status") or "uncertain")] += 1
        decisive = counts["helpful"] + counts["ineffective"] + counts["harmful"]
        helpful_rate = counts["helpful"] / decisive if decisive else 0.0
        if counts["harmful"] or (decisive >= 2 and helpful_rate < 0.5):
            support = "conflicting"
        elif len(sessions) >= min_independent_sessions and helpful_rate >= 0.7:
            support = "supported"
        elif len(sessions) >= 2:
            support = "emerging"
        else:
            support = "anecdotal"
        confidences = [float(item.get("resolution_confidence") or 0.0) for item in items]
        representative = max(
            items,
            key=lambda item: float(item.get("resolution_confidence") or 0.0),
        )
        reports.append({
            "candidate_id": f"candidate:{lesson_key[:16]}",
            "lesson_key": lesson_key,
            "title": str(representative.get("failure_type") or "反思经验候选")[:120],
            "target_capability": str(representative.get("failed_tool") or "general"),
            "support_status": support,
            "independent_sessions": len(sessions),
            "observation_count": len(items),
            "helpful_count": counts["helpful"],
            "ineffective_count": counts["ineffective"],
            "harmful_count": counts["harmful"],
            "uncertain_count": counts["uncertain"],
            "helpful_rate": round(helpful_rate, 4),
            "confidence": round(fmean(confidences), 4) if confidences else 0.0,
            "generalized_lesson": str(representative.get("generalized_lesson") or "")[:600],
            "evidence_refs": sorted({
                f"reflection:{item.get('reflection_id')}"
                for item in items if item.get("reflection_id")
            })[:20],
            "suggested_future_action": (
                "进入 Skill diff 提议阶段" if support == "supported"
                else "继续观察并收集独立任务证据"
            ),
            "applied": False,
        })
    return sorted(
        reports,
        key=lambda item: (
            {"supported": 0, "conflicting": 1, "emerging": 2, "anecdotal": 3}[item["support_status"]],
            -item["independent_sessions"],
            -item["confidence"],
        ),
    )


def build_success_workflow_candidates(
    observations: list[dict], *, min_independent_sessions: int = 3,
) -> list[dict]:
    """Aggregate generalized successful workflows across independent sessions."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for observation in observations:
        if not observation.get("eligible_for_learning"):
            continue
        key = str(observation.get("workflow_key") or "")
        if key:
            grouped[key].append(observation)

    reports: list[dict] = []
    for workflow_key, items in grouped.items():
        sessions = {str(item.get("session_id") or "") for item in items}
        sessions.discard("")
        confidences = [float(item.get("confidence") or 0.0) for item in items]
        reusabilities = [float(item.get("reusability") or 0.0) for item in items]
        confidence = fmean(confidences) if confidences else 0.0
        reusability = fmean(reusabilities) if reusabilities else 0.0
        if (
            len(sessions) >= min_independent_sessions
            and confidence >= 0.7
            and reusability >= 0.7
        ):
            support = "supported"
        elif len(sessions) >= 2:
            support = "emerging"
        else:
            support = "anecdotal"
        representative = max(
            items,
            key=lambda item: (
                float(item.get("confidence") or 0.0)
                + float(item.get("reusability") or 0.0)
            ),
        )
        existing_matches = [
            str(item.get("existing_skill_match"))
            for item in items if item.get("existing_skill_match")
        ]
        suggested_skill = max(set(existing_matches), key=existing_matches.count) if existing_matches else None
        reports.append({
            "candidate_id": f"workflow:{workflow_key[:16]}",
            "candidate_type": "successful_workflow",
            "workflow_key": workflow_key,
            "title": str(representative.get("workflow_name") or "成功工作流候选")[:160],
            "workflow_family": str(representative.get("workflow_family") or "")[:160],
            "summary": str(representative.get("summary") or "")[:600],
            "when_to_use": str(representative.get("when_to_use") or "")[:600],
            "steps": representative.get("steps") or [],
            "verification_steps": representative.get("verification_steps") or [],
            "suggested_existing_skill": suggested_skill,
            "suggested_proposal_type": "patch" if suggested_skill else "create",
            "support_status": support,
            "independent_sessions": len(sessions),
            "observation_count": len(items),
            "confidence": round(confidence, 4),
            "reusability": round(reusability, 4),
            "evidence_refs": sorted({
                f"success:{item.get('run_id')}" for item in items if item.get("run_id")
            })[:20],
            "suggested_future_action": (
                "生成 Skill diff 提议" if support == "supported"
                else "继续收集独立成功任务证据"
            ),
            "applied": False,
        })
    return sorted(
        reports,
        key=lambda item: (
            {"supported": 0, "emerging": 1, "anecdotal": 2}[item["support_status"]],
            -item["independent_sessions"],
            -item["confidence"],
        ),
    )
