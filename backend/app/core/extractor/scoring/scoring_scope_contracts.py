"""评分 scope 合约类型——不可变数据结构与枚举定义。

职责: 承载 ScopeIdentity、ScoringValidationKind、ScoringScopeExpectation、ScoringScopeValidation
      及其报告转换方法；所有类型均为不可变（frozen dataclass / MappingProxyType）。
不做: 不执行评分校验逻辑、不访问数据库、不调用 LLM、不引入 scoring_scope_validator。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from app.core.extractor.scoring.score_totals import ScoreTotalsReport

# 归一后的 scope 标识: (scheme, lot)；调用方需先归一化，禁止 None 或空字符串。
ScopeIdentity = tuple[str, str]


class ScoringValidationKind(str, Enum):
    """评分事实校验分类。"""

    COMPLETE = "complete"
    EVIDENCE_GAP = "evidence_gap"
    SCOPE_CONFLICT = "scope_conflict"
    TRANSFORMATION_LOSS = "transformation_loss"


@dataclass(frozen=True)
class ScoringScopeExpectation:
    """单个 scheme×lot scope 的确定性期望基准（只来自显式声明）。

    职责: 承载外部权威声明的 scope 期望，供 L3 目标筛选和审计闭环使用。
    不做: 不从已提取总分反推期望（漏项自证闭环）；不存储内部默认 100 分假设。
    """

    expected_total: float | None = None
    expected_by_volume: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.expected_total is None and not self.expected_by_volume:
            raise ValueError(
                "ScoringScopeExpectation 至少须有效: expected_total 或 expected_by_volume 之一非空"
            )
        if self.expected_total is not None and self.expected_total <= 0:
            raise ValueError(f"expected_total 必须 >0，实际: {self.expected_total}")
        if self.expected_by_volume:
            keys = [k for k, _ in self.expected_by_volume]
            if len(keys) != len(set(keys)):
                raise ValueError(f"expected_by_volume volume key 不得重复: {keys}")
            bad = [v for _, v in self.expected_by_volume if v <= 0]
            if bad:
                raise ValueError(f"expected_by_volume 期望值必须 >0，违规值: {bad}")


def _get_authoritative_expected_total(
    expected_by_scope: Mapping[ScopeIdentity, ScoringScopeExpectation],
) -> float | None:
    """从 expected_by_scope 提取唯一权威 expected_total；无一致值时返回 None。"""
    totals = {
        e.expected_total
        for e in expected_by_scope.values()
        if e.expected_total is not None
    }
    if len(totals) == 1:
        return next(iter(totals))
    return None


@dataclass(frozen=True)
class ScopeVolumeDelta:
    """逐卷双向对账差异(SchemeReader 阶段3)。

 volume="" 表示 scope 总集; expected=None 表示该卷无权威基准(方向 unknown)。
 delta>0 为缺口(deficit), delta<0 为盈余(surplus)——盈余是 add_fact 类救援
 在数学上不可能修复的结构失真信号(死锁根源)。
 """

    scope: ScopeIdentity
    volume: str
    actual: float
    expected: float | None
    tolerance: float = 1.0

    @property
    def delta(self) -> float | None:
        if self.expected is None:
            return None
        return round(self.expected - self.actual, 4)

    @property
    def direction(self) -> str:
        """deficit(缺口)/surplus(盈余)/balanced(闭合)/unknown(无基准)。"""
        d = self.delta
        if d is None:
            return "unknown"
        if abs(d) <= self.tolerance:
            return "balanced"
        return "deficit" if d > 0 else "surplus"


@dataclass(frozen=True)
class ScoringScopeValidation:
    """评分事实 scope 校验结果（不可变）。

    failed_scopes: EVIDENCE_GAP 时可归因到 scheme×lot 的失败 scope 列表（排序稳定、已去重）。
    expected_by_scope: 只读 Mapping，仅含显式权威声明的期望基准；内部默认 100 不写入此处。
    scope_deltas: 逐卷双向对账明细（阶段3）；COMPLETE 时为空。

    __post_init__ 强制不变量:
    - expected_by_scope 深复制并包为 MappingProxyType（防止源 dict 后续变更污染）。
    - failed_scopes 标准化为排序去重 tuple。
    """

    kind: ScoringValidationKind
    needs_review: bool
    note: str
    total: float
    totals_by_lot: dict[str, float] = field(default_factory=dict)
    failed_scopes: tuple[ScopeIdentity, ...] = ()
    expected_by_scope: Mapping[ScopeIdentity, ScoringScopeExpectation] = field(
        default_factory=lambda: MappingProxyType({})
    )
    scope_deltas: tuple[ScopeVolumeDelta, ...] = ()

    def __post_init__(self) -> None:
        # 标准化 expected_by_scope → MappingProxyType（防外部 dict 污染）
        if not isinstance(self.expected_by_scope, MappingProxyType):
            object.__setattr__(
                self,
                "expected_by_scope",
                MappingProxyType(dict(self.expected_by_scope)),
            )
        # 标准化 failed_scopes → 排序去重 tuple
        normalized = tuple(sorted(set(self.failed_scopes)))
        if normalized != self.failed_scopes:
            object.__setattr__(self, "failed_scopes", normalized)

    def has_structural_distortion(self) -> bool:
        """是否存在盈余卷/超额总集——add_fact 类救援无法修复, 须走结构重判/人工。"""
        return any(d.direction == "surplus" for d in self.scope_deltas)

    def to_score_totals_report(
        self, base_report: ScoreTotalsReport | None = None
    ) -> ScoreTotalsReport:
        """转换为现有提取结果报告，供兼容消费者使用。

        expected_total 优先级: expected_by_scope 权威 > base_report > 100.0（compat）。
        统一裁判结论（total/within_tolerance/needs_review/note）由本 validation 决定，
        禁止 base_report 改写裁判结论；展示字段（by_volume/item_count/missing/
        volume_checks/weight_sum_ok）来自 base_report（若提供）。
        """
        auth_expected = _get_authoritative_expected_total(self.expected_by_scope)
        if auth_expected is not None:
            expected_total = auth_expected
        elif base_report is not None:
            expected_total = base_report.expected_total
        else:
            expected_total = 100.0

        if base_report is not None:
            return ScoreTotalsReport(
                total=self.total,
                by_volume=base_report.by_volume,
                expected_total=expected_total,
                within_tolerance=not self.needs_review,
                needs_review=self.needs_review,
                note=self.note,
                item_count=base_report.item_count,
                missing_score_count=base_report.missing_score_count,
                volume_checks=base_report.volume_checks,
                weight_sum_ok=base_report.weight_sum_ok,
            )
        return ScoreTotalsReport(
            total=self.total,
            by_volume={},
            expected_total=expected_total,
            within_tolerance=not self.needs_review,
            needs_review=self.needs_review,
            note=self.note,
            item_count=0,
            missing_score_count=0,
        )


__all__ = [
    "ScopeVolumeDelta",
    "ScoringScopeExpectation",
    "ScoringScopeValidation",
    "ScopeIdentity",
    "ScoringValidationKind",
]
