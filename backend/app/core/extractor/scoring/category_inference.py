"""评分项六类 category 推断单一权威源（换轴重构 P3-1）。

职责: 以单一关键词表推断评分项 category，并返回命中依据(basis)——零命中不再
      静默默认 TECH，而是 category 仍落 TECH（DB 枚举不变）但 basis=None，
      供前端归入"未分类"桶（fail-visible，防错桶等效静默漏提）。
不做: 不影响满分聚合、不做卷/组归属、不调用 LLM；LLM/人工路径的 basis
      由调用方显式标注（"llm"/"manual"），不经过本词表。
"""
from __future__ import annotations

# 优先级有序（教训: 同一业务口径单一权威源）；词表为三处历史实现的并集
# （scoring_table_parser / numeric_score_column_parser / embedded_score_parser）。
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PERF", ("业绩", "实绩", "类似项目", "合同")),
    ("STAFF", ("人员", "团队", "学历", "职称", "项目经理", "项目负责人")),
    ("QUAL", ("资质", "认证", "证书", "专利", "信用", "信誉")),
    ("COMM", ("价格", "报价", "评标价", "商务", "维保费", "费用", "付款")),
)

BASIS_LLM = "llm"
BASIS_MANUAL = "manual"


def infer_scoring_category(text: str) -> tuple[str, str | None]:
    """从文本推断 (category, basis_keyword)。

    零命中返回 ("TECH", None)——None 即"未分类"信号；命中时 basis 为首个
    命中关键词（供前端"按'X'归入Y类"提示）。
    """
    compact = (text or "").replace(" ", "")
    for category, keywords in _CATEGORY_KEYWORDS:
        for keyword in keywords:
            if keyword in compact:
                return category, keyword
    return "TECH", None


__all__ = ["BASIS_LLM", "BASIS_MANUAL", "infer_scoring_category"]
