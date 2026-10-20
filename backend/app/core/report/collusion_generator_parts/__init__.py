"""R2-7 串标审查报告主题拆分子包

按主题将 25 个 `_build_*` 方法拆为 5 个职责模块:

- `cover_builder`：封面专题
- `pair_section`：Pair 详情主题（pair_details / single_pair / dimension_table / findings / single_finding）
- `group_section`：Group 主题（group_overview / group_conclusion / group_details /
  single_group / group_findings_section / single_group_finding）
- `global_findings`：全局特征发现专题（build_global_findings_grouped）
- `conclusion_summary`：概览/结论/分布/矩阵/建议/声明（pair 报告专属"非 pair-detail"章节）

设计约定（沿用 R2-2 / R2-3 同款拆分模式）:

- 子模块函数命名：原 method 名去前导下划线（`_build_cover` → `build_cover`）
- 第一参数 `self` 保留不替换，子模块内通过 `self.xxx` 触发主类 thin forwarder
  保证 monkey patch + 跨主题 fallback 调用顺畅
- 主类 `CollusionReportGenerator` 25 个 `_build_*` thin forwarder 全保留，
  外部 import 路径与测试 monkey patch 锚点零变化
- 模块级常量（_RISK_LABELS / _RISK_COLORS / _DIM_NAMES 等 18 个）+ 5 个 module-level
  helper 全部留在 `app.core.report.collusion_generator` 主模块作为 re-export 锚点；
  子模块通过 `from app.core.report.collusion_generator import _RISK_LABELS, _score_level`
  直接 import 复用

参考:
- v2 §八 R2-7 卡片
- 同款模式: tender_extractor_parts / reviewer_parts
"""

from app.core.report.collusion_generator_parts import (  # noqa: F401
    conclusion_summary,
    cover_builder,
    global_findings,
    group_section,
    pair_section,
)

__all__ = [
    "cover_builder",
    "pair_section",
    "group_section",
    "global_findings",
    "conclusion_summary",
]
