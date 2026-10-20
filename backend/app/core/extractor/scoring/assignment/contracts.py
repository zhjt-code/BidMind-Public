"""评分指派内核数据契约——最小冻结版（P2-2）。

职责: 定义 EvidenceAtom、Declaration、CandidateFact、Assignment、InvariantReport
      五个不可变 frozen dataclass 及轻量 helper（make_physical_key）；
      供 P2~P5 各 producer 适配器与指派内核共享，是"开放世界证据→封闭世界采纳"
      管道的中间模型契约。
不做: 不执行指派/裁决/采纳逻辑；不访问 DB/LLM；不导入 app.api.*；
      不依赖具体 producer；不写 Requirement 模型。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# 常量与枚举集合
# ---------------------------------------------------------------------------

SourceChannel = Literal["table", "text", "ocr", "embedded", "formula", "llm_reader"]
"""来源通道类型字面量：table/text/ocr/embedded/formula/llm_reader。"""

FactType = Literal["base_score", "price_formula", "group_total", "deduction", "bonus"]
"""评分事实语义类型字面量。"""

AssignmentStatus = Literal["VERIFIED", "PROVISIONAL", "FAILED"]
"""指派/报告三态状态字面量。"""

VALID_FACT_TYPES: frozenset[str] = frozenset(
    {"base_score", "price_formula", "group_total", "deduction", "bonus"}
)
"""有效 fact_type 枚举集合：
  base_score   — 普通评分项（基础分）
  price_formula — 价格公式叶节点（不得与商务/技术/价格总分双计）
  group_total   — 父级/组合计行（不得与其子项双计）
  deduction     — 扣分项
  bonus         — 加分项
"""

VALID_ASSIGNMENT_STATUSES: frozenset[str] = frozenset(
    {"VERIFIED", "PROVISIONAL", "FAILED"}
)
"""有效三态状态集合：
  VERIFIED    — 零硬冲突、零残差、实质唯一解
  PROVISIONAL — 存在残差/冲突/多解，结果近似可用但需人工核对
  FAILED      — 无法构成有效指派（无证据/硬冲突无法消解）
"""


# ---------------------------------------------------------------------------
# EvidenceAtom — 物理证据原子
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceAtom:
    """不可变物理证据原子——文档中可定位的最小可信信号单元。

    职责: 承载单条物理证据的原始文本、坐标、来源通道与跨通道别名声明；
          所有字段只读，不解释语义。
    不做: 不解释证据含义；不执行指派/去别名合并；不调用 LLM/DB；不写 Requirement。

    physical_key 格式（由 make_physical_key 产出）:
      d{doc_id}:p{page}:{channel_prefix6}:{position_tag}:{text_hash16}
    """

    physical_key: str
    """唯一物理定位键，由 make_physical_key() 幂等产出。"""

    raw_text: str
    """原始文本，禁止截断或改写。"""

    page: int
    """物理页码，1-based。"""

    source_channel: str
    """来源通道：table / text / ocr / embedded / formula / llm_reader。"""

    bbox: tuple[float, ...] | None = None
    """包围框 (x0, y0, x1, y1)，来自版式解析时填充；纯文本通道可为 None。"""

    row_idx: int | None = None
    """表格行索引（0-based）；非表格通道为 None。"""

    col_idx: int | None = None
    """表格列索引（0-based）；非表格通道为 None。"""

    numeric_value: float | None = None
    """确定性提取的数值信号（如分值）；无确定性数值时为 None。"""

    aliases: tuple[str, ...] = ()
    """跨通道同源别名物理键集合（table/text/OCR 指向同一物理证据时互列）。
    跨通道去别名合并由 P2-4 producer 适配器执行，此处仅存储声明。"""


# ---------------------------------------------------------------------------
# Declaration — 分值声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Declaration:
    """文档中权威声明的分值结构——lot/scheme/volume 层级分值来源。

    职责: 承载从标题行、合计行或说明列中检测到的分值声明；
          供指派内核作为可信约束基准（条件化闭合的前提）。
    不做: 不验证声明是否自洽；不推断未声明 scope；
          不执行分值闭合；不修改 EvidenceAtom。

    ref 格式: d{doc_id}:p{page}:decl:{volume_or_kind}
    """

    ref: str
    """稳定声明引用键。"""

    lot: str
    """采购包标识；单包固定用 '__single__'。"""

    scheme: str
    """评分方案标识；单方案固定用 '__single__'。"""

    volume: str
    """卷/分类标识（commercial/technical/price 等）；无分卷用 'unspecified'。"""

    declared_total: float | None = None
    """显式声明的该 scope 总分；仅原文有明确数字时填写，禁止反推。"""

    declared_by_volume: tuple[tuple[str, float], ...] = ()
    """按卷声明的分值分解：((volume_name, score), ...)，顺序对应原文。"""

    authority: str = "deterministic"
    """证据来源权威性：deterministic（确定性解析）/ llm_assisted（LLM 辅助）。"""

    evidence_atom_keys: tuple[str, ...] = ()
    """支撑本声明的 EvidenceAtom.physical_key 键列表。"""


# ---------------------------------------------------------------------------
# CandidateFact — 候选评分事实
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateFact:
    """候选评分事实——经初步解释但尚未被内核采纳的评分项。

    职责: 承载评分项名称/分值/scope/语义类型/物理证据引用；
          允许多条候选事实指向同一 EvidenceAtom（多解情形）。
    不做: 不执行去重/合并/采纳裁决；不发明无物理证据的分值；
          不写 Requirement；不推断 scope。

    约束（__post_init__ 强制）:
      1. fact_type 必须在 VALID_FACT_TYPES 内。
      2. confidence 须在 [0.0, 1.0]。
      3. evidence_atom_keys 至少有 1 条物理证据引用。
    """

    candidate_key: str
    """稳定候选事实键（由 producer 计算，跨 session 幂等）。"""

    lot: str
    """采购包标识。"""

    scheme: str
    """评分方案标识。"""

    volume: str
    """卷/分类标识。"""

    fact_type: str
    """事实语义类型：base_score / price_formula / group_total / deduction / bonus。"""

    name: str
    """评分项名称（原文，禁止推断或改写）。"""

    description: str
    """评分规则描述（原文）。"""

    score_value: float
    """该项满分或分值。"""

    evidence_atom_keys: tuple[str, ...]
    """本事实的 EvidenceAtom.physical_key 键列表（至少 1 条）。"""

    source_channel: str
    """首要来源通道。"""

    confidence: float = 1.0
    """producer 对本候选的置信度，范围 [0.0, 1.0]。"""

    page_refs: tuple[int, ...] = ()
    """来源页码列表（去重有序，1-based）。"""

    def __post_init__(self) -> None:
        if self.fact_type not in VALID_FACT_TYPES:
            raise ValueError(
                f"CandidateFact.fact_type 必须是 {VALID_FACT_TYPES} 之一，"
                f"实际: {self.fact_type!r}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"CandidateFact.confidence 必须在 [0.0, 1.0]，实际: {self.confidence}"
            )
        if not self.evidence_atom_keys:
            raise ValueError(
                "CandidateFact.evidence_atom_keys 至少须有一条物理证据引用"
            )


# ---------------------------------------------------------------------------
# Assignment — 指派结果快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    """指派结果快照——内核对单个 scheme×lot scope 的采纳决定。

    职责: 承载已采纳事实键、残差原子、冲突原子和三态状态；
          不可变快照，供 InvariantReport 校验器消费。
    不做: 不执行指派算法；不修改 CandidateFact；
          不写 Requirement；不含裁决理由逻辑代码。

    约束（__post_init__ 强制）:
      status 必须在 VALID_ASSIGNMENT_STATUSES 内。
    """

    assignment_id: str
    """稳定指派 ID：asgn:{doc_id}:{seq}。"""

    lot: str
    """本次指派的采购包 scope。"""

    scheme: str
    """本次指派的评分方案 scope。"""

    assigned_fact_keys: tuple[str, ...]
    """已采纳的 CandidateFact.candidate_key 列表（内核输出）。"""

    residual_atom_keys: tuple[str, ...]
    """未被任何已采纳事实消费的 EvidenceAtom.physical_key（残差）。"""

    conflict_atom_keys: tuple[str, ...]
    """被多个不同事实同时消费的 EvidenceAtom.physical_key（重复消费冲突）。"""

    status: str
    """三态：VERIFIED / PROVISIONAL / FAILED。"""

    declaration_refs: tuple[str, ...] = ()
    """本次指派参考的 Declaration.ref 键列表。"""

    mode: str = "kernel"
    """执行模式：kernel（新指派内核）/ legacy（降级旧解析链）。"""

    def __post_init__(self) -> None:
        if self.status not in VALID_ASSIGNMENT_STATUSES:
            raise ValueError(
                f"Assignment.status 必须是 {VALID_ASSIGNMENT_STATUSES} 之一，"
                f"实际: {self.status!r}"
            )


# ---------------------------------------------------------------------------
# InvariantCheck — 单项不变量检查
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantCheck:
    """单项不变量检查结果（P3-3 七项不变量之一）。

    职责: 承载一条不变量检查的名称、是否通过及注释；供 InvariantReport 汇总。
    不做: 不执行检查逻辑，仅为数据结构。

    check_name 规范值（P3-3 定义的七项）:
      evidence_coverage   — 每项事实至少有一条物理证据支撑
      score_conservation  — scope 内分值之和满足可信声明约束（条件化）
      unique_consumption  — 每条物理证据最多被一个事实消费
      scope_isolation     — lot/scheme/volume 边界不串扰
      hierarchy_conservation — 父项总分不得与子项分值双计
      conditional_closure — 分区闭合（仅声明权威可信时适用）
      substantial_uniqueness — 无实质等价的第二可行解
    """

    check_name: str
    """不变量检查项名称。"""

    passed: bool
    """是否通过。"""

    note: str = ""
    """人可读说明（失败时记录差值/原因）。"""


# ---------------------------------------------------------------------------
# InvariantReport — 不变量校验报告
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantReport:
    """不变量校验报告——对 Assignment 的七项不变量逐项评估汇总。

    职责: 承载 VERIFIED/PROVISIONAL/FAILED 三态结论、逐项检查明细、
          残差键与冲突键列表；供 API/Banner 透传和 P3-5 UX 消费。
    不做: 不执行校验逻辑；不修改 Assignment；不写 Requirement。

    约束（__post_init__ 强制）:
      1. status 必须在 VALID_ASSIGNMENT_STATUSES 内。
      2. VERIFIED 状态下 violations 必须为空。
      3. VERIFIED 状态下 residual_atom_keys 必须为空。
    """

    assignment_id: str
    """对应 Assignment.assignment_id。"""

    status: str
    """三态：VERIFIED / PROVISIONAL / FAILED。"""

    checks: tuple[InvariantCheck, ...]
    """逐项不变量检查结果（顺序稳定，对应 P3-3 七项）。"""

    violations: tuple[str, ...]
    """硬冲突描述列表（人可读）；VERIFIED 时强制为空。"""

    residual_atom_keys: tuple[str, ...]
    """未被采纳消费的 EvidenceAtom.physical_key（残差）；VERIFIED 时强制为空。"""

    conflict_atom_keys: tuple[str, ...]
    """重复消费物理证据键（冲突）；VERIFIED 时允许为空。"""

    note: str = ""
    """人可读摘要，供 Banner/API 展示（如"商务卷缺 3 分，可能在第 5-6 页"）。"""

    def __post_init__(self) -> None:
        if self.status not in VALID_ASSIGNMENT_STATUSES:
            raise ValueError(
                f"InvariantReport.status 必须是 {VALID_ASSIGNMENT_STATUSES} 之一，"
                f"实际: {self.status!r}"
            )
        if self.status == "VERIFIED":
            if self.violations:
                raise ValueError(
                    "InvariantReport: VERIFIED 状态下 violations 必须为空，"
                    f"实际包含 {len(self.violations)} 条: {self.violations[:3]}"
                )
            if self.residual_atom_keys:
                raise ValueError(
                    "InvariantReport: VERIFIED 状态下 residual_atom_keys 必须为空，"
                    f"实际包含 {len(self.residual_atom_keys)} 条"
                )

    @property
    def is_verified(self) -> bool:
        """是否为零缺口唯一解（可直接落库）。"""
        return self.status == "VERIFIED"

    @property
    def is_provisional(self) -> bool:
        """是否为近似解（需 Banner 提示 + 人工核对入口）。"""
        return self.status == "PROVISIONAL"

    @property
    def is_failed(self) -> bool:
        """是否为无法指派（需 FAILED 告警 + 人工干预）。"""
        return self.status == "FAILED"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_physical_key(
    doc_id: int,
    page: int,
    source_channel: str,
    *,
    bbox: tuple[float, ...] | None = None,
    row_idx: int | None = None,
    col_idx: int | None = None,
    text: str = "",
) -> str:
    """生成跨通道稳定物理定位键（幂等，相同输入必须产出相同 key）。

    格式: d{doc_id}:p{page}:{channel6}:{position_tag}:{text_hash16}

      channel6      — source_channel 前 6 字符（区分 table/text/ocr/embedded 等）
      position_tag  — bbox 存在时取 bbox 前4元素的 sha256[:8]；
                      否则 row_idx/col_idx 存在时取 r{row}c{col}；
                      均无时取 pos0
      text_hash16   — text 的 sha256[:16]

    不同通道的 key 不同（跨通道去别名声明在 EvidenceAtom.aliases，
    实际合并由 P2-4 producer 适配器执行）。
    """
    channel_tag = source_channel[:6]

    if bbox is not None and len(bbox) >= 4:
        bbox_str = ",".join(f"{v:.2f}" for v in bbox[:4])
        position_tag = _hash8(bbox_str)
    elif row_idx is not None and col_idx is not None:
        position_tag = f"r{row_idx}c{col_idx}"
    else:
        position_tag = "pos0"

    text_hash = _hash16(text)
    return f"d{doc_id}:p{page}:{channel_tag}:{position_tag}:{text_hash}"


def _hash8(text: str) -> str:
    """sha256 前 8 字符（用于 bbox 位置摘要）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _hash16(text: str) -> str:
    """sha256 前 16 字符（用于文本内容摘要）。"""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "Assignment",
    "AssignmentStatus",
    "CandidateFact",
    "Declaration",
    "EvidenceAtom",
    "FactType",
    "InvariantCheck",
    "InvariantReport",
    "SourceChannel",
    "VALID_ASSIGNMENT_STATUSES",
    "VALID_FACT_TYPES",
    "make_physical_key",
]
