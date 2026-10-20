"""审查证据包全局预算。

职责: 在发送前对审查 Prompt 的全部可裁证据条目做统一总预算分配、分面保底、
 去重、逐项取舍与可见覆盖快照, 并提供发送前硬上限预检。
不做: 不检索、不访问 DB、不渲染业务 Prompt 骨架模板、不调用 LLM、不判符合度。

背景: 图片证据、独立补搜、原文佐证、账本、跨类别、知识库此前各自
设分段帽(或无帽)后直接字符串拼接, round 0 无统一总预算, 收缩只在 provider
返回超长 400 之后才发生 —— 超时类过载完全不受控。本模块把"广召回候选留库、
Prompt 只携带每分面最小充分证据"这一原则沉淀为纯函数。

复用而非新写:
- 取舍策略复用 ``input_budget.assemble_within_budget``(骨架永不裁/按优先级整块取舍)
- 分面保底复用 ``evidence_policy.select_facet_covered_rows``
- 可见覆盖复用 ``evidence_policy.facet_missing_keys_for_rows``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.core.ai.input_budget import (
    PromptBlock,
    assemble_within_budget,
    clip_text_keep_ends,
    estimate_prompt_tokens,
    prompt_hard_cap_violation,
)
from app.core.ai.reviewer_parts.evidence_policy import (
    facet_missing_keys_for_rows,
    select_facet_covered_rows,
)
from app.core.config import settings
from app.core.retriever.evidence_coverage import EvidenceFacet

# --------------------------------------------------------------------------
# 证据区（与既有 ReviewEvidenceBundle 字段一一对应, 渲染后回填原字段）
# --------------------------------------------------------------------------

SECTION_RAW = "raw"
SECTION_IMAGE = "image"
SECTION_INDEPENDENT = "independent"
SECTION_PROHIBITION = "prohibition"
SECTION_LEDGER = "ledger"
SECTION_CROSS = "cross"
SECTION_KNOWLEDGE = "knowledge"

_DIVIDER = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 区头逐字沿用既有实现, 保证 Prompt 语义与既有测试断言不变。
SECTION_HEADERS: dict[str, str] = {
    SECTION_RAW: (
        f"{_DIVIDER}\n"
        "【投标原文佐证（来自检索命中片段，按命中类型与相似度排序）】\n"
        "审查规则：若上方投标响应摘要与下方原文佐证在事实/数值/范围上不一致，"
        "以原文为准；并在 comment 中明确指出差异。原文佐证不可作为证明本要求被响应的"
        "唯一依据——仍需结合招标要求语义判断。"
    ),
    SECTION_IMAGE: (
        f"{_DIVIDER}\n"
        "【以下为投标响应来源页面的图片附件与证据摘要 -- 投标人可能以扫描件提供了证明材料】"
    ),
    SECTION_INDEPENDENT: (
        f"{_DIVIDER}\n"
        "【以下为系统独立搜索发现的投标文件文本/表格/图片证据 -- 请判断是否构成对本要求的响应】"
    ),
    SECTION_PROHIBITION: "",  # 违禁摘录自带标题, 不再叠加区头
    SECTION_LEDGER: "",       # 账本块自带 "## 项目证据账本" 标题
    SECTION_CROSS: "",        # 跨类别块自带说明行
    SECTION_KNOWLEDGE: "",    # 知识库文本自带块头
}

# 区默认优先级: 数值越小越先保留。分面代表与禁止项在组包时另行提权。
SECTION_PRIORITY: dict[str, int] = {
    SECTION_RAW: 10,
    SECTION_IMAGE: 20,
    SECTION_INDEPENDENT: 25,
    SECTION_LEDGER: 30,
    SECTION_CROSS: 40,
    SECTION_KNOWLEDGE: 60,
    SECTION_PROHIBITION: 5,
}

# 分面代表条目的提权值（高于任何区默认优先级）。
_FACET_REPRESENTATIVE_PRIORITY = 0

# 允许被截断的区: 整块文本(账本/跨类别/知识库)可裁剪保留部分;
# 检索类条目已按区上限裁过, 不再二次腰斩。
_CLIPPABLE_SECTIONS = frozenset({SECTION_LEDGER, SECTION_CROSS, SECTION_KNOWLEDGE})

# 渲染顺序: 与既有 Prompt 布局一致（图片/独立补搜/违禁 → 账本 → 原文 → 跨类别 → 知识库）
_RENDER_ORDER: tuple[str, ...] = (
    SECTION_IMAGE,
    SECTION_INDEPENDENT,
    SECTION_PROHIBITION,
    SECTION_LEDGER,
    SECTION_RAW,
    SECTION_CROSS,
    SECTION_KNOWLEDGE,
)

@dataclass(frozen=True)
class EvidenceItem:
    """一条可独立取舍的证据条目。

    ``text`` 是已渲染的单条文本(含页码/角色标签), ``content`` 是用于去重与分面
    归属判定的纯正文。``clippable=False`` 表示只能整条保留或整条丢弃。
    """

    section: str
    text: str
    content: str = ""
    label: str = ""
    page: int | None = None
    chunk_id: int | None = None
    document_id: int | None = None
    role: str = "primary"
    similarity: float = 0.0
    matched_sub_query: str | None = None
    clippable: bool = True

    @property
    def identity(self) -> tuple[Any, Any]:
        """去重键: (document_id, chunk_id); chunk_id 缺失时退化为内容前缀。"""
        if self.chunk_id is not None:
            return (self.document_id, self.chunk_id)
        normalized = "".join((self.content or self.text or "").split())
        return (self.document_id, f"text:{normalized[:60]}")

    def as_row(self) -> dict[str, Any]:
        """转成 evidence_policy 选择器与覆盖评估所需的行结构。"""
        return {
            "chunk_id": self.chunk_id,
            "role": self.role,
            "page": self.page,
            "similarity": self.similarity,
            "content": self.content or self.text,
            "matched_sub_query": self.matched_sub_query,
        }


@dataclass(frozen=True)
class EvidencePackBudget:
    """单次组包的预算切片（全部区共享 ``pack_chars`` 总帽）。"""

    pack_chars: int
    knowledge_chars: int
    ledger_chars: int
    item_chars: int
    item_max: int


@dataclass
class EvidencePackResult:
    """组包结果: 按区渲染文本 + 取舍可观测 + 可见覆盖快照。"""

    sections: dict[str, str] = field(default_factory=dict)
    kept_labels: tuple[str, ...] = ()
    dropped_labels: tuple[str, ...] = ()
    used_chars: int = 0
    budget_chars: int = 0
    candidate_count: int = 0
    visible_facet_missing: tuple[str, ...] = ()
    over_budget: bool = False

    def section_text(self, section: str) -> str:
        return self.sections.get(section, "")

    @property
    def image_evidence(self) -> str:
        """图片/独立补搜/违禁摘录合并回填既有 ``image_evidence`` 字段。"""
        parts = [
            self.sections.get(SECTION_IMAGE, ""),
            self.sections.get(SECTION_INDEPENDENT, ""),
            self.sections.get(SECTION_PROHIBITION, ""),
        ]
        return "\n".join(part for part in parts if part).strip("\n")

    @property
    def raw_evidence_excerpts(self) -> str:
        return self.sections.get(SECTION_RAW, "")

    @property
    def ledger_evidence(self) -> str:
        return self.sections.get(SECTION_LEDGER, "")

    @property
    def cross_category_evidence(self) -> str:
        return self.sections.get(SECTION_CROSS, "")

    @property
    def knowledge_context_text(self) -> str:
        return self.sections.get(SECTION_KNOWLEDGE, "")

    def as_log_meta(self) -> dict[str, Any]:
        return {
            "candidates": self.candidate_count,
            "kept": len(self.kept_labels),
            "dropped": len(self.dropped_labels),
            "used_chars": self.used_chars,
            "budget_chars": self.budget_chars,
            "visible_facet_missing": list(self.visible_facet_missing),
        }


def resolve_pack_budget(reduction_round: int = 0) -> EvidencePackBudget:
    """按收缩轮次解析证据包预算。

    语义迁移自 ``pair_review._review_evidence_budgets``: 知识库衰减更快(平方)
    并在低于 300 字符时整块放弃, 证据总帽衰减较慢并保底 800 字符。
    """
    ratio = min(max(float(settings.REVIEW_RETRY_REDUCTION_RATIO), 0.3), 0.9)
    rounds = max(0, int(reduction_round))

    base_pack = max(800, int(settings.REVIEW_EVIDENCE_PACK_CHARS))
    pack_chars = max(800, int(base_pack * (ratio ** rounds)))

    base_knowledge = max(0, int(settings.REVIEW_KNOWLEDGE_CHAR_BUDGET))
    knowledge_chars = int(base_knowledge * (ratio ** (2 * rounds)))
    if knowledge_chars < 300:
        knowledge_chars = 0

    base_ledger = max(0, int(settings.REVIEW_LEDGER_CHAR_BUDGET))
    ledger_chars = int(base_ledger * (ratio ** rounds))

    item_chars = max(120, int(settings.REVIEW_RAW_EVIDENCE_CHARS))
    item_max = max(1, int(settings.REVIEW_EVIDENCE_ITEM_MAX))

    return EvidencePackBudget(
        pack_chars=pack_chars,
        knowledge_chars=knowledge_chars,
        ledger_chars=ledger_chars,
        item_chars=item_chars,
        item_max=item_max,
    )


def _dedupe_items(items: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    """按 (document_id, chunk_id) 去重, 同一 chunk 保留区优先级最高的表达。

    同一 chunk 可能同时以图片摘要与原文佐证两种形态出现; 原文佐证带页码、
    角色与相似度标签, 信息密度更高, 因此按 ``SECTION_PRIORITY`` 取优,
    同优先级保留先出现者。输出保持候选原始顺序。
    """
    best: dict[tuple[Any, Any], int] = {}
    ordered: list[EvidenceItem] = []
    for item in items:
        if not (item.text or "").strip():
            continue
        ordered.append(item)

    for idx, item in enumerate(ordered):
        identity = item.identity
        incumbent = best.get(identity)
        if incumbent is None:
            best[identity] = idx
            continue
        challenger_rank = SECTION_PRIORITY.get(item.section, 100)
        incumbent_rank = SECTION_PRIORITY.get(ordered[incumbent].section, 100)
        if challenger_rank < incumbent_rank:
            best[identity] = idx

    keep = set(best.values())
    return [item for idx, item in enumerate(ordered) if idx in keep]


def _facet_representative_ids(
    items: Sequence[EvidenceItem],
    facets: Sequence[EvidenceFacet],
) -> set[int]:
    """选出每个分面的代表条目, 返回其在 ``items`` 中的下标集合。

    只对带正文的检索类条目做分面归属判定; 账本/知识库/跨类别等整块文本不参与
    代表选择(它们本就单条, 由区优先级决定去留)。

    下标通过行内 ``_item_index`` 透传: ``select_facet_covered_rows`` 原样返回
    行对象引用, 该键不参与其排序与命中判定, 因此可安全携带。
    """
    facet_tuple = tuple(facets or ())
    if not facet_tuple:
        return set()

    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if item.section in (SECTION_LEDGER, SECTION_KNOWLEDGE, SECTION_CROSS):
            continue
        row = item.as_row()
        row["_item_index"] = idx
        rows.append(row)

    if not rows:
        return set()

    selected = select_facet_covered_rows(rows, facet_tuple, len(facet_tuple))
    return {
        int(row["_item_index"])
        for row in selected
        if row.get("_item_index") is not None
    }


def _clip_item_text(item: EvidenceItem, budget: EvidencePackBudget) -> str:
    """单条目先按区上限裁剪, 避免个别超长 chunk 顶爆总预算。"""
    if item.section == SECTION_KNOWLEDGE:
        limit = budget.knowledge_chars
    elif item.section == SECTION_LEDGER:
        limit = budget.ledger_chars
    elif not item.clippable:
        return item.text
    else:
        # 单条检索证据的正文上限 + 标签开销余量
        limit = budget.item_chars + 160
    if limit <= 0:
        return ""
    if len(item.text) <= limit:
        return item.text
    return clip_text_keep_ends(item.text, limit)


def assemble_evidence_pack(
    items: Sequence[EvidenceItem],
    facets: Sequence[EvidenceFacet] = (),
    budget: EvidencePackBudget | None = None,
) -> EvidencePackResult:
    """在统一总预算内组装证据包: 去重 → 分面保底 → 优先级取舍 → 按区渲染。

    Args:
        items: 全部候选证据条目(广召回结果, 可远多于最终可见集)
        facets: 当前要求的分面契约, 用于保底与可见覆盖复算
        budget: 预算切片; 省略时取 round 0 预算

    Returns:
        EvidencePackResult: 各区渲染文本 + 取舍可观测 + 可见覆盖缺口
    """
    effective_budget = budget or resolve_pack_budget(0)
    candidates = _dedupe_items(items)
    result = EvidencePackResult(
        budget_chars=effective_budget.pack_chars,
        candidate_count=len(candidates),
    )
    if not candidates:
        return result

    representatives = _facet_representative_ids(candidates, facets)

    # 阶段1: 逐条目裁到区上限, 并绑定优先级(分面代表提权到最高)。
    prepared: list[tuple[EvidenceItem, str, PromptBlock]] = []
    for idx, item in enumerate(candidates):
        clipped = _clip_item_text(item, effective_budget)
        if not clipped.strip():
            continue
        priority = (
            _FACET_REPRESENTATIVE_PRIORITY
            if idx in representatives
            else SECTION_PRIORITY.get(item.section, 100)
        )
        label = item.label or f"{item.section}#{idx}"
        prepared.append((
            item,
            clipped,
            PromptBlock(
                text=clipped,
                priority=priority,
                is_skeleton=False,
                label=label,
                # 检索类条目已按区上限裁过, 整条保留或整条丢弃; 半截证据对判断
                # 无价值且可能误导。整块类(账本/知识库/跨类别)允许截断。
                clippable=item.clippable and item.section in _CLIPPABLE_SECTIONS,
            ),
        ))

    # 阶段2: 条目数上限——按 (优先级, 原顺序) 取前 N, 其余记为超量丢弃。
    ordered = sorted(range(len(prepared)), key=lambda i: (prepared[i][2].priority, i))
    allowed = set(ordered[: effective_budget.item_max])
    over_count_labels = tuple(prepared[i][2].label for i in ordered[effective_budget.item_max:])

    budgeted = [prepared[i] for i in range(len(prepared)) if i in allowed]
    assembly = assemble_within_budget(
        [block for _, _, block in budgeted], effective_budget.pack_chars
    )

    # 阶段3: 按 assembly 决策回填文本; 被截断的整块类按实际剩余额度重裁。
    kept_set = set(assembly.kept_labels)
    truncated_set = set(assembly.truncated_labels)
    kept_full = [text for _, text, block in budgeted if block.label in kept_set]
    # 预留每块之间的换行分隔符, 与 assemble_within_budget 的记账口径一致
    fixed_chars = sum(len(text) for text in kept_full) + len(kept_full)
    remaining = max(0, effective_budget.pack_chars - fixed_chars)

    kept_texts: dict[str, list[str]] = {}
    kept_items: list[EvidenceItem] = []
    for item, text, block in budgeted:
        if block.label in kept_set:
            rendered = text
        elif block.label in truncated_set:
            rendered = clip_text_keep_ends(text, remaining)
            remaining = max(0, remaining - len(rendered))
            if not rendered.strip():
                continue
        else:
            continue
        kept_texts.setdefault(item.section, []).append(rendered)
        kept_items.append(item)

    sections: dict[str, str] = {}
    for section in _RENDER_ORDER:
        parts = kept_texts.get(section)
        if not parts:
            continue
        header = SECTION_HEADERS.get(section, "")
        body = "\n".join(parts)
        sections[section] = f"{header}\n{body}" if header else body

    result.sections = sections
    result.kept_labels = tuple(assembly.kept_labels) + tuple(assembly.truncated_labels)
    result.dropped_labels = tuple(assembly.dropped_labels) + over_count_labels
    result.used_chars = sum(len(text) for text in sections.values())
    result.over_budget = assembly.over_budget
    if facets:
        result.visible_facet_missing = facet_missing_keys_for_rows(
            facets, [item.as_row() for item in kept_items]
        )
    return result


# 发送前硬上限预检（estimate_prompt_tokens / prompt_hard_cap_violation）
# 已下沉至 app.core.ai.input_budget 供审查/提取链共用，
# 此处保留 re-export 维持既有调用方与测试的导入路径不变。


__all__ = [
    "EvidenceItem",
    "EvidencePackBudget",
    "EvidencePackResult",
    "SECTION_CROSS",
    "SECTION_HEADERS",
    "SECTION_IMAGE",
    "SECTION_INDEPENDENT",
    "SECTION_KNOWLEDGE",
    "SECTION_LEDGER",
    "SECTION_PROHIBITION",
    "SECTION_RAW",
    "assemble_evidence_pack",
    "estimate_prompt_tokens",
    "prompt_hard_cap_violation",
    "resolve_pack_budget",
]
