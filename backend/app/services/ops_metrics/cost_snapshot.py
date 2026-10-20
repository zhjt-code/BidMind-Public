"""ops_metrics.cost_snapshot: 跨项目成本摘要 (R2-8 拆分产物).

职责: 按窗口汇总跨项目用量，逐项目套计费规则后按流程归堆。
不做: 实扣流水、财务总览、漏斗健康。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.llm_execution_trace import LlmExecutionTrace
from app.models.project import Project

COST_FLOW_ORDER = ("bid_writing", "bid_review", "collusion_check")


def _empty_flow_row(flow: str) -> dict[str, Any]:
    """构造一条全 0 的流程行。"""
    return {
        "flow": flow,
        "project_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_credits": 0,
        "project_avg_credits": 0.0,
    }


def _avg_credits(total_credits: int, project_count: int) -> float:
    """窗口或行内加权均点。"""
    return round(total_credits / project_count, 1) if project_count else 0.0


def _canonical_flow(raw_type: object | None) -> str | None:
    """把项目类型收成三流程键；无法归类返回 None。"""
    value = getattr(raw_type, "value", raw_type)
    key = str(value) if value is not None else ""
    return key if key in COST_FLOW_ORDER else None


async def _quote_project_credits(db: AsyncSession, metrics: dict[str, int]) -> int:
    """公开版本不计点。保留调用点，避免改动用量汇总。"""
    del db, metrics
    return 0


async def _load_project_types(
    db: AsyncSession,
    project_ids: list[int],
) -> dict[int, object]:
    """批量读取项目类型；空集合不查库。"""
    if not project_ids:
        return {}
    rows = (
        await db.execute(
            select(Project.id, Project.project_type).where(Project.id.in_(project_ids))
        )
    ).all()
    return {int(project_id): project_type for project_id, project_type in rows}


async def cost_snapshot(
    *,
    db: AsyncSession,
    cs: datetime,
    bs: datetime,
) -> dict[str, Any]:
    """近窗口跨项目成本摘要.

    项目均点按"项目窗口内实际用量 -> 逐项目套用计费规则 -> 再求均值"计算，
    避免直接用聚合 token/pages 反推时被保底或封顶规则误导。
    """
    del bs

    page_rows = (await db.execute(
        select(
            Document.project_id,
            func.coalesce(func.sum(Document.page_count), 0),
            func.coalesce(func.sum(Document.ocr_image_count), 0),
        )
        .where(Document.created_at >= cs)
        .group_by(Document.project_id)
    )).all()

    token_rows = (await db.execute(
        select(
            LlmExecutionTrace.project_id,
            func.coalesce(func.sum(LlmExecutionTrace.total_prompt_tokens), 0),
            func.coalesce(func.sum(LlmExecutionTrace.total_completion_tokens), 0),
        )
        .where(LlmExecutionTrace.created_at >= cs)
        .where(LlmExecutionTrace.project_id.is_not(None))
        .group_by(LlmExecutionTrace.project_id)
    )).all()

    project_metrics: dict[int, dict[str, int]] = {}

    for project_id, page_count, ocr_image_count in page_rows:
        metrics = project_metrics.setdefault(
            int(project_id),
            {"page_count": 0, "ocr_image_count": 0, "input_tokens": 0, "output_tokens": 0},
        )
        metrics["page_count"] = int(page_count or 0)
        metrics["ocr_image_count"] = int(ocr_image_count or 0)

    for project_id, input_tokens, output_tokens in token_rows:
        if project_id is None:
            continue
        metrics = project_metrics.setdefault(
            int(project_id),
            {"page_count": 0, "ocr_image_count": 0, "input_tokens": 0, "output_tokens": 0},
        )
        metrics["input_tokens"] = int(input_tokens or 0)
        metrics["output_tokens"] = int(output_tokens or 0)

    type_by_id = await _load_project_types(db, list(project_metrics.keys()))
    flow_totals = {flow: _empty_flow_row(flow) for flow in COST_FLOW_ORDER}
    unknown_project_count = 0
    total_credits_cur = 0

    for project_id, metrics in project_metrics.items():
        credits = await _quote_project_credits(db, metrics)
        total_credits_cur += credits
        flow = _canonical_flow(type_by_id.get(project_id))
        if flow is None:
            unknown_project_count += 1
            continue
        row = flow_totals[flow]
        row["project_count"] += 1
        row["input_tokens"] += metrics["input_tokens"]
        row["output_tokens"] += metrics["output_tokens"]
        row["total_credits"] += credits

    for row in flow_totals.values():
        row["project_avg_credits"] = _avg_credits(int(row["total_credits"]), int(row["project_count"]))

    page_cur = sum(metrics["page_count"] for metrics in project_metrics.values())
    tokens_in_cur = sum(metrics["input_tokens"] for metrics in project_metrics.values())
    tokens_out_cur = sum(metrics["output_tokens"] for metrics in project_metrics.values())
    project_cur = len(project_metrics)
    credit_yuan_rate = 0.0

    return {
        "page_count": int(page_cur),
        "input_tokens": int(tokens_in_cur),
        "output_tokens": int(tokens_out_cur),
        "project_count": int(project_cur),
        "tokens_per_project": int((tokens_in_cur + tokens_out_cur) / project_cur) if project_cur else 0,
        "tokens_per_page": int((tokens_in_cur + tokens_out_cur) / page_cur) if page_cur else 0,
        "project_avg_credits": _avg_credits(total_credits_cur, project_cur),
        "total_credits": int(total_credits_cur),
        "credit_yuan_rate": float(credit_yuan_rate),
        "estimated_yuan": round(total_credits_cur * credit_yuan_rate, 2),
        "unknown_project_count": int(unknown_project_count),
        "by_flow": [flow_totals[flow] for flow in COST_FLOW_ORDER],
    }
