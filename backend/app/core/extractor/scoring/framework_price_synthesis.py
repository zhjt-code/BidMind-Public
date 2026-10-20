"""框架表价格公式事实合成（缺口B, 样本1形态）。

职责: 当框架表(卷定义表)的价格行"评分标准"列携带完整价格公式时, 以卷定义满分为
      权威分值合成一条 price 卷评分事实。
不做: 不从公式文本猜分值(满分只取"总分N分"卷定义)、不解析明细表、不调用 LLM/DB。

根因(2026-07-17 样本审计): "评审办法前附表"型文档的价格评分规则整段写在框架表
标准列内(技术明细另册引用), 原框架表路径只产卷定义不产事实, 价格60分权重的
公式证据被静默丢弃, 全文档提取0项。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from app.schemas.requirement import ExtractedScoringItem

_FORMULA_RE = re.compile(r"得分\s*=")
_FORMULA_TERMS = ("基准价", "评标价", "评审价格", "评标总价")
_PRICE_ITEM_NAME = "报价评分"
_RULE_MAX_CHARS = 1600
# 卷定义声明句("价格总分100分，按60%权重进行折算。")从规则文本剥离:
# 它已解析为卷定义, 留在 description 会被 split_definitions_and_items
# 误判整项为"卷定义行"而剥离(层), 合成事实静默消失。
_VOLUME_DEF_SENTENCE_RE = re.compile(
    r"[^。；;]{0,8}总分\s*\d+(?:\.\d+)?\s*分[^。；;]{0,20}?按\s*\d+(?:\.\d+)?\s*[%％][^。；;]{0,12}?折算[。；;]?"
)


def synthesize_framework_price_item(
    volume_defs: dict[str, Any],
    price_rule_text: str,
) -> ExtractedScoringItem | None:
    """按价格卷定义与其标准列公式文本合成 price 评分事实; 证据不足返回 None。

    生效条件(全部满足): price 卷定义存在且 volume_max>0; 标准列文本含"得分="
    公式与基准价类词(与 price_formula_recognizer 同一判据, 防说明性短句误合成)。
    """
    price_def = volume_defs.get("price")
    if price_def is None:
        return None
    volume_max = float(getattr(price_def, "volume_max", 0) or 0)
    if volume_max <= 0:
        return None
    compact = re.sub(r"\s+", "", str(price_rule_text or ""))
    if not compact:
        return None
    if not _FORMULA_RE.search(compact) or not any(t in compact for t in _FORMULA_TERMS):
        return None
    rule = _VOLUME_DEF_SENTENCE_RE.sub("", str(price_rule_text or "")).strip()[:_RULE_MAX_CHARS]
    if len(rule) < 10:
        return None
    try:
        return ExtractedScoringItem(
            category="COMM",
            score_item_name=_PRICE_ITEM_NAME,
            description=f"{_PRICE_ITEM_NAME} {rule}".strip(),
            score_weight=volume_max,
            volume="price",
            volume_label=getattr(price_def, "label", None),
            score_rules=[rule],
        )
    except ValidationError:
        return None


__all__ = ["synthesize_framework_price_item"]
