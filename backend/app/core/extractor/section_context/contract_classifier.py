"""合同/拟签合同章节分类器（P0）。

职责: 判定一段招标文件内容是否属于"拟签合同/合同格式文本"区段，
      用于把合同条款从"投标人招标要求"中隔离（标记为 informational，不进审查打分）。
不做: 不物理删除内容、不调用 LLM、不访问 DB；不接管商务要求(付款/质保/报价)的正常提取。

设计要点（区别于 writer 的 `_CONTRACT_PHASE_KEYWORDS` 宽口径）:
- 审查侧只想隔离"整段拟签合同文本"，因此以**章节标题强信号**为主：
  section_path 命中"合同条款/合同格式/合同协议书/拟签合同/合同范本"等。
- 仅当章节信号缺失时，才用**合同正文多标记**兜底（甲方/乙方/违约责任/争议解决/
  本合同自……生效 等需同时出现 >= 阈值），避免把含"付款方式""质保期"的单条
  正常商务要求误判为合同文本。
- 负向护栏：命中"业绩/类似项目/合同金额"等 PERF 语义时一律判非合同，
  防止把"类似合同业绩要求"误隔离。
"""

from __future__ import annotations

from dataclasses import dataclass

# 章节标题强信号：出现在 section_path 即判定为合同区段
_CONTRACT_TITLE_KEYWORDS: tuple[str, ...] = (
    "合同条款",
    "合同格式",
    "合同协议书",
    "协议书格式",
    "拟签合同",
    "拟签订合同",
    "拟签订的合同",
    "合同主要条款",
    "合同条件",
    "通用合同条款",
    "专用合同条款",
    "合同文本",
    "合同范本",
    "合同书格式",
)

# 章节标题弱信号：含"合同/协议"且搭配下列结构词，组合判定为合同区段
_CONTRACT_TITLE_BASE: tuple[str, ...] = ("合同", "协议书")
_CONTRACT_TITLE_STRUCT: tuple[str, ...] = ("条款", "格式", "文本", "范本", "协议书", "附件")

# 合同正文标记：章节信号缺失时的兜底，需同时命中 >= 阈值
_CONTRACT_BODY_MARKERS: tuple[str, ...] = (
    "甲方",
    "乙方",
    "本合同",
    "违约责任",
    "争议解决",
    "不可抗力",
    "本协议",
    "双方授权代表",
    "签字盖章之日起生效",
    "合同生效",
    "赔偿金",
)
_CONTRACT_BODY_MIN_HITS = 3

# 负向护栏：命中即判非合同（PERF/业绩语义，"类似合同业绩"不应被隔离）
_PERFORMANCE_GUARD_KEYWORDS: tuple[str, ...] = (
    "业绩",
    "类似项目",
    "类似工程",
    "合同金额",
    "中标通知",
    "项目经验",
)


@dataclass(frozen=True)
class SectionPhase:
    """章节阶段判定结果。

    Attributes:
        is_contract: 是否属于拟签合同/合同格式区段。
        reason: 命中依据（便于日志与回查）。
    """

    is_contract: bool
    reason: str


def classify_section_phase(
    section_path: str | None,
    content: str | None = None,
) -> SectionPhase:
    """判定内容是否属于拟签合同/合同格式区段。

    Args:
        section_path: chunk 的章节路径（chunk_planner 由标题行回填）。
        content: chunk 正文（用于章节信号缺失时的兜底判定）。

    Returns:
        SectionPhase: is_contract + 命中依据。
    """
    section = (section_path or "").strip()
    body = content or ""

    # 负向护栏优先：业绩/类似项目语境一律判非合同
    guard_scope = f"{section} {body[:200]}"
    if any(kw in guard_scope for kw in _PERFORMANCE_GUARD_KEYWORDS):
        return SectionPhase(is_contract=False, reason="performance_guard")

    # 强信号：章节标题直接命中合同关键词
    for kw in _CONTRACT_TITLE_KEYWORDS:
        if kw in section:
            return SectionPhase(is_contract=True, reason=f"title:{kw}")

    # 弱信号：章节标题含"合同/协议书"且搭配结构词
    if any(base in section for base in _CONTRACT_TITLE_BASE) and any(
        struct in section for struct in _CONTRACT_TITLE_STRUCT
    ):
        return SectionPhase(is_contract=True, reason="title_combo")

    # 兜底：章节信号缺失时，合同正文多标记同时出现
    hits = sum(1 for marker in _CONTRACT_BODY_MARKERS if marker in body)
    if hits >= _CONTRACT_BODY_MIN_HITS:
        return SectionPhase(is_contract=True, reason=f"body_markers:{hits}")

    return SectionPhase(is_contract=False, reason="not_contract")
