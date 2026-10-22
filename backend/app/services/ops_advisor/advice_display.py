"""
职责: 周度调度建议的静态中文标签与列表展示字段装配
不做: 不做规则计算、不写回 priority、不做指标聚合
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ops_metrics.llm_dimension_labels import (
    PURPOSE_LABELS,
    resolve_dimension_labels,
)

ADVICE_ACTION_LABELS: dict[str, str] = {
    "promote": "提升为主用",
    "demote": "降权",
    "keep": "保持",
    "insufficient_data": "数据不足",
}

ADVICE_STATUS_LABELS: dict[str, str] = {
    "pending": "待确认",
    "applied": "已写回",
    "rolled_back": "已回滚",
    "dismissed": "已忽略",
}

ADVICE_RULE_LABELS: dict[str, str] = {
    "no_candidates": "无可用配置候选",
    "all_insufficient_sample": "样本均不足",
    "promote_best_valid_rate": "最优有效率达标，建议提升",
    "best_already_preferred": "最优已是首选，保持",
    "demote_worst_degraded": "最差劣化，建议降权",
    "no_action_needed": "暂无需调整",
    "node_insufficient_sample": "节点样本不足",
    "node_readonly_best": "节点级只读建议",
    "test_promote": "测试提升规则",
}


def format_advice_action_label(action: Optional[str]) -> str:
    if not action:
        return "未知动作"
    return ADVICE_ACTION_LABELS.get(action, "未知动作")


def format_advice_status_label(status: Optional[str]) -> str:
    if not status:
        return "未知状态"
    return ADVICE_STATUS_LABELS.get(status, "未知状态")


def format_advice_rule_label(rule_id: Optional[str]) -> str:
    if not rule_id:
        return "未知规则"
    return ADVICE_RULE_LABELS.get(rule_id, "未知规则")


async def enrich_weekly_advice_items(
    db: AsyncSession,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """为周建议列表附加中文展示字段。"""
    if not items:
        return items

    node_keys: list[Optional[str]] = []
    config_ids: list[Optional[str]] = []
    for item in items:
        if item.get("scope") == "node":
            node_keys.append(item.get("target"))
        config_ids.append(item.get("recommended_config_id"))
        config_ids.append(item.get("demote_config_id"))

    node_labels = await resolve_dimension_labels(
        db, group_by="node", raw_keys=node_keys,
    )
    config_labels = await resolve_dimension_labels(
        db, group_by="config", raw_keys=config_ids,
    )

    enriched: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        scope = row.get("scope")
        target = row.get("target")
        if scope == "purpose":
            row["target_display"] = PURPOSE_LABELS.get(str(target or ""), str(target or "未采集"))
        elif scope == "node":
            label = node_labels.get(target) or {}
            row["target_display"] = label.get("display_name") or target or "未采集"
        else:
            row["target_display"] = str(target or "未采集")

        rec = row.get("recommended_config_id")
        dem = row.get("demote_config_id")
        row["recommended_config_display"] = (
            (config_labels.get(rec) or {}).get("display_name") if rec else None
        )
        row["demote_config_display"] = (
            (config_labels.get(dem) or {}).get("display_name") if dem else None
        )
        row["advice_action_label"] = format_advice_action_label(row.get("advice_action"))
        row["status_label"] = format_advice_status_label(row.get("status"))
        row["rule_label"] = format_advice_rule_label(row.get("rule_id"))
        enriched.append(row)
    return enriched
