"""
职责: 把 llm_attempt_events 的维度原始值解析为运维可读的展示名与定位线索
不做: 不做指标聚合、不做百分比/颜色等前端格式化、不写任何库表
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_node import LlmNode

LABEL_STATE_LABELED = "labeled"
LABEL_STATE_NOT_COLLECTED = "not_collected"
LABEL_STATE_UNRECOGNIZED = "unrecognized"

PURPOSE_LABELS: dict[str, str] = {
    "review": "审查",
    "vision": "视觉",
    "judge": "评判",
    "extract": "提取",
}

PROVIDER_LABELS: dict[str, str] = {
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "zhipu": "智谱",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "moonshot": "月之暗面",
    "siliconflow": "硅基流动",
    "volcengine": "火山引擎",
    "openrouter": "OpenRouter",
    "aihubmix": "AiHubMix",
    "tencent": "腾讯",
    "baidu": "百度",
    "local": "本地",
}

FLOW_LABELS: dict[str, str] = {
    "bid_review": "投标审查",
    "collusion": "串标审查",
    "writing": "标书编写",
    "judge": "评判复核",
}

_UNRECOGNIZED_SENTINELS = frozenset({"unknown", "unknown_429"})


def classify_raw_key(raw_key: Optional[str]) -> str:
    """把原始维度值归入 labeled / not_collected / unrecognized 三态。"""
    if raw_key is None or raw_key == "":
        return LABEL_STATE_NOT_COLLECTED
    if raw_key in _UNRECOGNIZED_SENTINELS:
        return LABEL_STATE_UNRECOGNIZED
    return LABEL_STATE_LABELED


def _static_label(group_by: str, raw_key: str) -> Optional[str]:
    if group_by == "purpose":
        return PURPOSE_LABELS.get(raw_key)
    if group_by == "provider":
        return PROVIDER_LABELS.get(raw_key)
    if group_by == "flow":
        return FLOW_LABELS.get(raw_key)
    return None


def _config_lookup() -> dict[str, dict[str, str]]:
    """从 AI 设置加载 config_id -> {model_name, purpose} 索引。"""
    try:
        from app.core.ai_settings import load_ai_settings

        settings = load_ai_settings()
    except Exception:
        return {}

    index: dict[str, dict[str, str]] = {}
    for purpose in ("review", "vision", "judge", "extract"):
        configs = getattr(settings, f"{purpose}_configs", None) or []
        for cfg in configs:
            cid = getattr(cfg, "id", None)
            if not cid:
                continue
            index[str(cid)] = {
                "model_name": str(getattr(cfg, "model_name", "") or ""),
                "purpose": purpose,
                "config_type": str(getattr(cfg, "config_type", "") or ""),
            }
    return index


def _infer_purpose_from_config_id(config_id: str) -> Optional[str]:
    for purpose in ("review", "vision", "judge", "extract"):
        if config_id.startswith(f"{purpose}_"):
            return purpose
    return None


async def _load_node_names(db: AsyncSession, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    rows = (
        await db.execute(
            select(LlmNode.node_key, LlmNode.name).where(LlmNode.node_key.in_(keys))
        )
    ).all()
    return {str(row[0]): str(row[1]) for row in rows if row[0]}


def _build_entry(
    *,
    raw_key: Optional[str],
    display_name: str,
    secondary: Optional[str] = None,
    label_state: str,
) -> dict[str, Any]:
    return {
        "raw_key": raw_key,
        "display_name": display_name,
        "secondary": secondary,
        "label_state": label_state,
    }


async def resolve_dimension_labels(
    db: AsyncSession,
    *,
    group_by: str,
    raw_keys: list[Optional[str]],
) -> dict[Optional[str], dict[str, Any]]:
    """按 group_by 维度批量解析展示名。

    返回 map: raw_key -> {raw_key, display_name, secondary, label_state}
    """
    unique_keys = list(dict.fromkeys(raw_keys))
    result: dict[Optional[str], dict[str, Any]] = {}

    node_names: dict[str, str] = {}
    config_index: dict[str, dict[str, str]] = {}
    if group_by == "node":
        labeled_keys = [
            k for k in unique_keys
            if classify_raw_key(k) == LABEL_STATE_LABELED and k is not None
        ]
        node_names = await _load_node_names(db, labeled_keys)
    elif group_by == "config":
        config_index = _config_lookup()

    for raw in unique_keys:
        state = classify_raw_key(raw)
        if state == LABEL_STATE_NOT_COLLECTED:
            result[raw] = _build_entry(
                raw_key=raw,
                display_name="未采集",
                secondary=None,
                label_state=state,
            )
            continue
        if state == LABEL_STATE_UNRECOGNIZED:
            if group_by == "provider":
                display = "未识别厂商"
            else:
                display = "未识别"
            result[raw] = _build_entry(
                raw_key=raw,
                display_name=display,
                secondary=raw,
                label_state=state,
            )
            continue

        assert raw is not None
        if group_by == "node":
            name = node_names.get(raw)
            result[raw] = _build_entry(
                raw_key=raw,
                display_name=name or raw,
                secondary=raw if name else None,
                label_state=LABEL_STATE_LABELED if name else LABEL_STATE_UNRECOGNIZED,
            )
            if not name:
                result[raw]["display_name"] = f"未登记节点 ({raw})"
            continue

        if group_by == "config":
            info = config_index.get(raw)
            purpose = (
                (info or {}).get("purpose")
                or _infer_purpose_from_config_id(raw)
            )
            purpose_zh = PURPOSE_LABELS.get(purpose or "", purpose or "")
            model_name = (info or {}).get("model_name") or ""
            if model_name and purpose_zh:
                display = f"{model_name} · {purpose_zh}"
            elif model_name:
                display = model_name
            elif purpose_zh:
                display = f"{purpose_zh}配置"
            else:
                display = raw
            result[raw] = _build_entry(
                raw_key=raw,
                display_name=display,
                secondary=raw,
                label_state=LABEL_STATE_LABELED,
            )
            continue

        if group_by == "model":
            result[raw] = _build_entry(
                raw_key=raw,
                display_name=raw,
                secondary=None,
                label_state=LABEL_STATE_LABELED,
            )
            continue

        static = _static_label(group_by, raw)
        result[raw] = _build_entry(
            raw_key=raw,
            display_name=static or raw,
            secondary=raw if static else None,
            label_state=LABEL_STATE_LABELED if static else LABEL_STATE_UNRECOGNIZED,
        )
        if not static and group_by in ("purpose", "provider", "flow"):
            result[raw]["display_name"] = f"未识别 ({raw})"

    return result


async def enrich_breakdown_rows(
    db: AsyncSession,
    *,
    group_by: Optional[str],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """给 breakdown 行附加 display_name / secondary / label_state / raw_key。"""
    if not group_by or not rows:
        return rows

    raw_keys = [row.get("raw_key") for row in rows]
    labels = await resolve_dimension_labels(db, group_by=group_by, raw_keys=raw_keys)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get("raw_key")
        label = labels.get(raw) or _build_entry(
            raw_key=raw,
            display_name=row.get("key") or "未采集",
            secondary=None,
            label_state=classify_raw_key(raw if isinstance(raw, str) else None),
        )
        enriched.append({**row, **label})
    return enriched
