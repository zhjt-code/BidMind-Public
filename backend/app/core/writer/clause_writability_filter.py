"""约束写作可操作性过滤器

职责: 对 LLM 提取的约束按"能否通过投标文件内容编写来满足或规避"做规则级兜底过滤，
      剔除纯行为性废标条款(串通/多份投标/逾期/撤回)，这些条款在标书正文中永远无法
      体现响应，属于审标视角而非写标视角的噪音。
不做: 不修改约束内容、不做语义分类/作用域判定、不访问 DB/LLM、不处理去重、
      不触碰 submission_rule / submission_notice(其收窄由提取 Prompt 负责，
      过滤器介入易误杀白名单并破坏素材页注意事项清单)。
"""

from __future__ import annotations

from loguru import logger

from app.schemas.writing import ExtractedClause

# 100% 无法通过投标文件正文内容响应的行为/流程违规特征(投标人行为违规)。
# 谨慎起见不含"失信/税收违法/弄虚作假"——这些可通过附信用/纳税证明响应，属可写。
_BEHAVIORAL_PATTERNS: tuple[str, ...] = (
    "串通", "围标", "陪标",
    "2个或者2个以上", "两个或两个以上", "2个以上", "两份以上",
    "多个投标文件", "多份投标文件", "不同的投标文件",
    "不同投标报价", "不同的投标报价",
    "逾期送达", "逾期递交", "逾期到达", "逾期送到",
    "撤回投标", "撤销投标", "撤回其投标",
)

# 可写信号：命中任一则强制保留(防误杀内容性废标，如超限价/★条款/未提供材料/技术负偏离)。
_WRITABLE_EXEMPTIONS: tuple[str, ...] = (
    "★", "▲", "☆",
    "最高限价", "招标控制价", "最高投标限价", "报价超",
    "未提供", "未按要求提供", "缺少", "应包含", "须包含", "负偏离",
    "技术参数", "技术指标", "实质性要求",
)


def _is_pure_behavioral_disqualification(clause: ExtractedClause) -> bool:
    """判断是否为纯行为性废标条款(仅作用于 disqualification 子类型)。"""
    if getattr(clause, "sub_type", "") != "disqualification":
        return False
    text = f"{clause.content or ''} {clause.raw_text or ''}"
    if any(token in text for token in _WRITABLE_EXEMPTIONS):
        return False
    return any(token in text for token in _BEHAVIORAL_PATTERNS)


def split_writable_clauses(
    clauses: list[ExtractedClause],
) -> tuple[list[ExtractedClause], list[ExtractedClause]]:
    """将约束按写作可操作性拆分为 (保留, 剔除)。

    仅剔除 disqualification 中命中行为性特征且无可写信号的条款；
    其余 sub_type 全部原样保留。对每条剔除项输出 WARNING 供人工核对误杀。
    """
    kept: list[ExtractedClause] = []
    dropped: list[ExtractedClause] = []
    for clause in clauses:
        if _is_pure_behavioral_disqualification(clause):
            dropped.append(clause)
            logger.warning(
                "[WritabilityFilter] 剔除纯行为性废标条款(标书正文无法响应): content={!r}",
                (getattr(clause, "content", "") or "")[:80],
            )
        else:
            kept.append(clause)
    return kept, dropped


__all__ = ["split_writable_clauses"]
