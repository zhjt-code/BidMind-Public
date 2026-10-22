"""审查输出预算的截断判定与扩容。

职责: 依据 Pydantic 校验错误判定"疑似输出被 max_tokens 截断", 并给出下一轮重试
 的扩容预算; 供 call_llm_review 的校验重试循环单点消费。
不做: 不调用 LLM、不执行校验、不构造或压缩 prompt、不读写 DB。

背景: 评分项审查 prompt 额外要求 comment 200-400 字与 rule_assessments 数组,
输出量约为普通要求的 3~4 倍(实测普通要求 comment 中位 113 字)。共用节点预算
1500 时输出撞上限被截断, cloud_client 抢救出部分字段后必填字段缺失, 校验必败;
而重试仅追加提示语、预算不变, 构成同模型+同预算+低温的确定性重放(同源)。
"""
from __future__ import annotations

from typing import Any, Iterable

# 扩容上限, 与 cloud_client.chat_with_validation 的截断扩容天花板保持同一量级。
REVIEW_TOKEN_CEILING = 8192

# 截断特征错误类型: 必填字段缺失。
# 判据说明: prompt 已显式列出全部必填字段, LLM 在预算充足时不会整段漏写; 一旦输出
# 被截断, 尾部字段与数组元素必然缺失并报 missing。故 missing 是截断的强信号。
# 其余类型(如 string_too_long / literal_error)属内容不合规, 扩容无用, 不触发。
_TRUNCATION_ERROR_TYPES: frozenset[str] = frozenset({"missing"})


def looks_like_truncated_output(errors: Iterable[Any] | None) -> bool:
    """校验错误是否呈现"输出被截断"特征（存在必填字段缺失即判是）。

    容错: 逐条目取值异常按"非截断"处理——本判定只用于决定是否扩容重试,
    误判为非截断只是退回原有行为, 不得因判定本身抛错中断审查。
    """
    if not errors:
        return False
    for err in errors:
        try:
            if isinstance(err, dict) and err.get("type") in _TRUNCATION_ERROR_TYPES:
                return True
        except Exception:  # noqa: BLE001 判定边界防御, 失败即视为非截断
            continue
    return False


def escalate_review_max_tokens(current: int) -> int:
    """截断后的下一轮预算: 翻倍并封顶; 已达顶或非法入参返回封顶值。"""
    try:
        base = int(current)
    except (TypeError, ValueError):
        return REVIEW_TOKEN_CEILING
    if base <= 0:
        return REVIEW_TOKEN_CEILING
    return min(base * 2, REVIEW_TOKEN_CEILING)


__all__ = [
    "REVIEW_TOKEN_CEILING",
    "escalate_review_max_tokens",
    "looks_like_truncated_output",
]
