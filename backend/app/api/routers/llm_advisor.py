"""
周度 LLM 调度建议 API（独立 router，避免继续堆叠 admin_ops）。

职责: 薄转发读建议 / 二次确认写回 / 回滚
不做: 不做规则计算、不直接操作 SQLAlchemy 查询构造
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, require_admin
from app.models.user import User
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin/ops/llm/advice", tags=["运维监控-周度建议"])


class ApplyAdviceRequest(BaseModel):
    expected_fingerprint: str = Field(
        ...,
        min_length=8,
        max_length=64,
        description="客户端所见的当前 priority 指纹",
    )


@router.get("", response_model=ApiResponse)
async def list_advice(
    status: Optional[str] = None,
    limit: int = 50,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    from app.services.ops_advisor.weekly_reliability_report import list_weekly_advice

    items = await list_weekly_advice(db, status=status, limit=min(max(limit, 1), 100))
    return ApiResponse(code=200, message="success", data={"items": items, "total": len(items)})


@router.post("/generate", response_model=ApiResponse)
async def trigger_generate_advice(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """管理员手动触发本周分析（不等待周日 cron）。"""
    from app.services.ops_advisor.weekly_reliability_report import (
        generate_and_persist_weekly_advice,
    )

    data = await generate_and_persist_weekly_advice(db)
    return ApiResponse(code=200, message="已生成周建议", data=data)


@router.get("/{advice_id}/preview", response_model=ApiResponse)
async def preview_advice_apply(
    advice_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """写回前预览：返回 before priority 与指纹。"""
    from app.services.ops_advisor.priority_apply_service import (
        AdviceApplyError,
        get_current_priority_fingerprint,
    )
    from app.services.ops_advisor.weekly_reliability_report import get_advice_by_id

    advice = await get_advice_by_id(db, advice_id)
    if advice is None:
        return ApiResponse(code=404, message="建议不存在", data=None)
    if advice.scope != "purpose":
        return ApiResponse(code=400, message="节点级建议不可写回", data=None)
    try:
        current = await get_current_priority_fingerprint(advice.target)
    except AdviceApplyError as e:
        return ApiResponse(code=400, message=e.message, data={"error_code": e.code})
    return ApiResponse(
        code=200,
        message="success",
        data={
            "advice_id": advice.id,
            "purpose": advice.target,
            "advice_action": advice.advice_action,
            "proposed_priority": advice.proposed_priority,
            "current": current,
            "rule_id": advice.rule_id,
            "evidence_json": advice.evidence_json,
        },
    )


@router.post("/{advice_id}/apply", response_model=ApiResponse)
async def apply_advice(
    advice_id: int,
    body: ApplyAdviceRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    from app.services.ops_advisor.priority_apply_service import (
        AdviceApplyError,
        apply_purpose_advice,
    )

    try:
        data = await apply_purpose_advice(
            db,
            advice_id=advice_id,
            expected_fingerprint=body.expected_fingerprint,
            username=admin.username,
            user_id=admin.id,
        )
        return ApiResponse(code=200, message="已应用建议", data=data)
    except AdviceApplyError as e:
        code = 409 if e.code == "fingerprint_mismatch" else 400
        return ApiResponse(code=code, message=e.message, data={"error_code": e.code})


@router.post("/{advice_id}/rollback", response_model=ApiResponse)
async def rollback_advice(
    advice_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    from app.services.ops_advisor.priority_apply_service import (
        AdviceApplyError,
        rollback_purpose_advice,
    )

    try:
        data = await rollback_purpose_advice(
            db,
            advice_id=advice_id,
            username=admin.username,
            user_id=admin.id,
        )
        return ApiResponse(code=200, message="已回滚", data=data)
    except AdviceApplyError as e:
        return ApiResponse(code=400, message=e.message, data={"error_code": e.code})
