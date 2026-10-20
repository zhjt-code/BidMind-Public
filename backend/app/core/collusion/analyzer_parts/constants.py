"""串标分析器模块级常量与基础工具

Task 8 Step 1：从 analyzer.py 迁出。所有符号通过 analyzer.py 顶部 re-export
对外暴露，外部 `from app.core.collusion.analyzer import _xxx` 路径零变化。

收纳范围：
- 自定义异常：ExtractionQualityError
- 阈值/上限常量：9 个
- 顶部小工具：3 个（_shrink_*、_trim_*）

不在本模块的"顶部小工具"：
- `_get_collusion_system`: 依赖 `get_registry()`，留主 analyzer.py
- `_has_positive_dimension_score` / `_has_dimension_payload`: 与 findings 域强相关，
  推迟到 Step 4 与 findings.py 一起迁
"""

from __future__ import annotations

from typing import Any

from app.core.collusion.dimension_keys import COLLUSION_DIM_KEYS


class ExtractionQualityError(Exception):
    """提取质量门控异常：失败比例超过阈值时抛出"""
    pass


# 提取质量门控阈值：失败比例超过此值则中止分析（允许至少1份失败后仍继续）
_EXTRACTION_FAIL_THRESHOLD = 0.5

# 单文件最大提取文本字符数（避免超出上下文窗口）
_MAX_EXTRACT_CHARS = 15000
# 其中图片证据区独立预算（从 _MAX_EXTRACT_CHARS 中划拨）
_IMAGE_BUDGET = 3000
# 图片标注 chunk 的识别前缀（由 image_extractor 生成）
# 招标文件最大格式提取字符数
_MAX_TENDER_CHARS = 10000

# 八维度键名到(代号, 标签)的映射 -- 标签文案本地维护，key 与真相源严格同步
_DIM_MAP: dict[str, tuple[str, str]] = {
    "d1_personnel":     ("D1", "D1 人员重叠"),
    "d2_company":       ("D2", "D2 企业关联"),
    "d3_text":          ("D3", "D3 技术方案雷同"),
    "d4_price":         ("D4", "D4 报价异常"),
    "d5_qualification": ("D5", "D5 资质业绩重叠"),
    "d6_format":        ("D6", "D6 文档格式"),
    "d7_abnormal":      ("D7", "D7 异常一致性"),
    "d8_metadata":      ("D8", "D8 文档元数据"),
}
# key 序列引用真相源 dimension_keys.COLLUSION_DIM_KEYS（S.2.7），
# import 时断言 _DIM_MAP 键与其完全一致，防止两处手写漂移
assert tuple(_DIM_MAP.keys()) == COLLUSION_DIM_KEYS, (
    "_DIM_MAP 键序列与 dimension_keys.COLLUSION_DIM_KEYS 真相源不一致"
)
_DIM_KEYS = COLLUSION_DIM_KEYS

# certainty 门控阈值: 低于此值的维度不参与风险判定，不列入证据
_CERTAINTY_MIN = 0.3
_DECISION_ENGINE_VERSION = 2

_RISK_RANK_LOCAL: dict[str, int] = {
    "high": 4, "medium": 3, "low": 2, "none": 1, "inconclusive": 0,
}


def _shrink_list_semantically(items: list[Any], *, min_items: int = 1) -> list[Any]:
    """按当前优先级顺序收缩列表，保留前部高价值语义单元。"""
    if len(items) <= min_items:
        return list(items)
    drop_count = max(1, len(items) // 3)
    keep_count = max(min_items, len(items) - drop_count)
    return list(items[:keep_count])


def _shrink_text_blocks(text: str, *, min_blocks: int = 1) -> str:
    """按空行分块收缩上下文，保留完整语义块。"""
    if not text.strip():
        return text

    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    if len(blocks) <= min_blocks:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if len(lines) <= min_blocks:
            return text
        kept_lines = _shrink_list_semantically(lines, min_items=min_blocks)
        return "\n".join(kept_lines)

    kept_blocks = _shrink_list_semantically(blocks, min_items=min_blocks)
    return "\n\n".join(kept_blocks)


def _trim_pair_results_for_summary(pair_results: list[dict], *, min_items: int = 3) -> list[dict]:
    """为综合研判保留高风险 pair，并同步收缩 findings 数量。"""
    ranked = sorted(
        [res for res in pair_results if isinstance(res, dict)],
        key=lambda item: _RISK_RANK_LOCAL.get(str(item.get("overall_risk", "inconclusive")).lower(), 0),
        reverse=True,
    )
    kept = _shrink_list_semantically(ranked, min_items=min_items)

    trimmed: list[dict] = []
    for item in kept:
        copied = dict(item)
        findings = copied.get("key_findings") or []
        copied["key_findings"] = findings[: max(1, min(2, len(findings)))]
        trimmed.append(copied)
    return trimmed


# 分组检索配置 -- entity(D1/D2) 和 business(D4/D5) 各自的语义查询与关键词
_DIMENSION_RETRIEVAL: dict[str, dict] = {
    "entity": {
        "queries": [
            "项目经理 技术负责人 安全员 质量员 人员配置表",
            "法定代表人 注册地址 联系电话 统一社会信用代码 银行账户 营业执照",
        ],
        "keywords": ["项目经理", "技术负责", "身份证", "资格证", "法定代表人", "注册地址", "联系电话", "营业执照", "银行"],
    },
    "business": {
        "queries": [
            "投标报价 投标总价 单价分析表 工程量清单 费率",
            "类似工程业绩 合同金额 项目案例 资质证书 获奖",
        ],
        "keywords": ["投标报价", "总价", "单价", "工程量", "费率", "业绩", "合同金额", "资质", "获奖"],
    },
}
