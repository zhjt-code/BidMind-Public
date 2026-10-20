"""串标八维度 key 常量真相源（S.2.7 新建）。

历史缺陷（A6）：FindingPageQuoteVerifyGate 内部维度白名单错写为
``d4_pricing``，而主流程 analyzer / prompts / report 实际用的是 ``d4_price``，
导致 D4 维度的 findings 页码回查从未触发。

所有涉及维度 key 字面量的模块必须 import 本常量，禁止再手写 ``d1_...`` / ``d4_..._pattern``
之类的字符串，防止复发。
"""

from __future__ import annotations


COLLUSION_DIM_KEYS: tuple[str, ...] = (
    "d1_personnel",
    "d2_company",
    "d3_text",
    "d4_price",
    "d5_qualification",
    "d6_format",
    "d7_abnormal",
    "d8_metadata",
)
"""八维度对比结果 compare_result 内的字段名（与 analyzer / prompts / report 严格对齐）。"""


COLLUSION_DIM_KEYS_SET: frozenset[str] = frozenset(COLLUSION_DIM_KEYS)
