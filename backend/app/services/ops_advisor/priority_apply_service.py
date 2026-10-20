"""
职责: 经二次确认把周建议写回用途池 priority, 并提供快照审计与回滚
不做: 不向 ai_settings.py 新增方法(复用 set_pool_priority)、不改节点级路由
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai_settings import Purpose, load_ai_settings, set_pool_priority
from app.models.llm_priority_snapshot import LlmPrioritySnapshot
from app.models.llm_weekly_advice import LlmWeeklyAdvice
from app.services.audit_service import audit_log


def fingerprint_priority(priority: dict[str, int]) -> str:
    """稳定指纹：按 key 排序后 JSON sha256 前 16 位。"""
    normalized = {str(k): int(v) for k, v in sorted(priority.items())}
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class AdviceApplyError(Exception):
    """写回/回滚业务错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


async def _load_advice(db: AsyncSession, advice_id: int) -> LlmWeeklyAdvice:
    advice = (
        await db.execute(
            select(LlmWeeklyAdvice).where(LlmWeeklyAdvice.id == advice_id)
        )
    ).scalar_one_or_none()
    if advice is None:
        raise AdviceApplyError("not_found", f"建议不存在: {advice_id}")
    return advice


async def apply_purpose_advice(
    db: AsyncSession,
    *,
    advice_id: int,
    expected_fingerprint: str,
    username: str,
    user_id: Optional[int] = None,
) -> dict[str, Any]:
    """二次确认后写回用途池 priority。"""
    advice = await _load_advice(db, advice_id)
    if advice.scope != "purpose":
        raise AdviceApplyError("not_writable", "节点级建议不可写回，仅供参考")
    if advice.status != "pending":
        raise AdviceApplyError("invalid_status", f"当前状态不可应用: {advice.status}")
    if not advice.proposed_priority:
        raise AdviceApplyError("no_proposal", "该建议无可写回的 priority 提案")
    if advice.advice_action not in ("promote", "demote"):
        raise AdviceApplyError(
            "no_change",
            f"动作 {advice.advice_action} 无需写回",
        )

    purpose = advice.target
    if purpose not in ("vision", "review", "judge", "extract"):
        raise AdviceApplyError("invalid_purpose", f"非法用途: {purpose}")

    settings = load_ai_settings()
    before = dict(settings.get_pool_priority(purpose))  # type: ignore[arg-type]
    before_fp = fingerprint_priority(before)
    if before_fp != expected_fingerprint:
        raise AdviceApplyError(
            "fingerprint_mismatch",
            "当前 priority 已变化，请刷新后重新确认",
        )

    snapshot = LlmPrioritySnapshot(
        purpose=purpose,
        priority_json=before,
        fingerprint=before_fp,
        reason="advice_apply",
        advice_id=advice.id,
        created_by=username,
    )
    db.add(snapshot)
    await db.flush()

    after = {str(k): int(v) for k, v in advice.proposed_priority.items()}
    try:
        await set_pool_priority(purpose, after)  # type: ignore[arg-type]
    except ValueError as e:
        raise AdviceApplyError("invalid_priority", str(e)) from e

    advice.status = "applied"
    advice.applied_snapshot_id = snapshot.id
    advice.applied_at = datetime.now(timezone.utc)
    advice.applied_by = username
    result = {
        "advice_id": advice.id,
        "purpose": purpose,
        "before": before,
        "after": after,
        "snapshot_id": snapshot.id,
        "status": advice.status,
        "rule_id": advice.rule_id,
    }
    await db.commit()

    await audit_log(
        db,
        user_id=user_id,
        username=username,
        category="llm_advisor",
        action="apply_priority_advice",
        resource_type="llm_weekly_advice",
        resource_id=advice_id,
        detail=json.dumps(
            {
                "advice_id": advice_id,
                "purpose": purpose,
                "before": before,
                "after": after,
                "snapshot_id": result["snapshot_id"],
                "rule_id": result["rule_id"],
            },
            ensure_ascii=False,
        ),
    )

    return result


async def rollback_purpose_advice(
    db: AsyncSession,
    *,
    advice_id: int,
    username: str,
    user_id: Optional[int] = None,
) -> dict[str, Any]:
    """按应用时快照回滚 priority。"""
    advice = await _load_advice(db, advice_id)
    if advice.status != "applied":
        raise AdviceApplyError("invalid_status", f"当前状态不可回滚: {advice.status}")
    if not advice.applied_snapshot_id:
        raise AdviceApplyError("no_snapshot", "缺少可回滚快照")

    snapshot = (
        await db.execute(
            select(LlmPrioritySnapshot).where(
                LlmPrioritySnapshot.id == advice.applied_snapshot_id
            )
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise AdviceApplyError("snapshot_missing", "快照记录不存在")

    purpose = advice.target
    settings = load_ai_settings()
    current = dict(settings.get_pool_priority(purpose))  # type: ignore[arg-type]
    restore = {str(k): int(v) for k, v in (snapshot.priority_json or {}).items()}

    # 回滚前再拍一张当前状态，便于审计
    rollback_snap = LlmPrioritySnapshot(
        purpose=purpose,
        priority_json=current,
        fingerprint=fingerprint_priority(current),
        reason="rollback",
        advice_id=advice.id,
        created_by=username,
    )
    db.add(rollback_snap)
    await db.flush()

    try:
        await set_pool_priority(purpose, restore)  # type: ignore[arg-type]
    except ValueError as e:
        raise AdviceApplyError("invalid_priority", str(e)) from e
    advice.status = "rolled_back"
    result = {
        "advice_id": advice.id,
        "purpose": purpose,
        "before": current,
        "after": restore,
        "status": advice.status,
    }
    await db.commit()

    await audit_log(
        db,
        user_id=user_id,
        username=username,
        category="llm_advisor",
        action="rollback_priority_advice",
        resource_type="llm_weekly_advice",
        resource_id=advice_id,
        detail=json.dumps(
            {
                "advice_id": advice_id,
                "purpose": purpose,
                "restored_from_snapshot_id": snapshot.id,
                "before": current,
                "after": restore,
            },
            ensure_ascii=False,
        ),
    )

    return result


async def get_current_priority_fingerprint(purpose: str) -> dict[str, Any]:
    """供前端二次确认展示 before 与指纹。"""
    if purpose not in ("vision", "review", "judge", "extract"):
        raise AdviceApplyError("invalid_purpose", f"非法用途: {purpose}")
    settings = load_ai_settings()
    priority = dict(settings.get_pool_priority(purpose))  # type: ignore[arg-type]
    return {
        "purpose": purpose,
        "priority": priority,
        "fingerprint": fingerprint_priority(priority),
    }
