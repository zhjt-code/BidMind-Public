"""评分方案(scheme)确定性切分（, 阶段2）。

职责: 在评分 chunk 有序处理流中判定"当前 chunk 属于当前评分方案还是开启新方案",
 并给解析出的评分项盖 scheme 标签。
不做: 不解析表格、不去重、不做闭合校验裁决(scoring_scope_validator 职责)、不调用 LLM/DB。

切分信号(保守 AND, 歧义不切):
- 上一方案已闭合(单一百分制合计≈100, 或加权制各卷合计=卷基准);
- 新 chunk 产出新评分事实(每个 scoring_table chunk 天然是表头锚定的独立逻辑表);
- 新 chunk 表格指纹与当前方案内任一表不同(指纹相同=页面/文档副本, 交既有吸收去重,
 禁止把副本切成"另一套方案"造成重复计分合法化)。

 Task 5 修订（C3/C4）:
- boundary→scheme 映射: A→B→A 回访时复用 A 的 scheme（C3）；
- 结构边界分支前先执行指纹副本检查: 同指纹跨边界仍视为副本（C4）。

根因(2026-07-17 样本审计): 样本9同文档连续两套独立百分制(技术100+商务100)无 scheme
维度被混算为 200/100; 21份合并压力件跨样本互相吸收损失29项/306分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SINGLE_SCHEME = "__single__"
_SCHEME_PREFIX = "scheme_"
# 治理阶段5 消歧: 原名 _CLOSURE_TOLERANCE 与 row_group_attribution/table_scope_resolution
# 中的同名常量取值相差 100 倍(0.01 vs 1.0), 属"同名不同义"暗雷。仅改名不改值。
# 本处是**方案级百分制闭合**容差(整卷 100 分制允许 1 分误差), 与行级父子闭合的
# 0.01 分容差语义不同。
_SCHEME_CLOSURE_TOLERANCE = 1.0
_EXPECTED_TOTAL = 100.0


def scheme_display_label(scheme: str | None, *, total_schemes: int) -> str:
    """scheme 标识 → 用户可读中文名("评分方案N"); 单方案返回空串(不打扰既有文案)。"""
    if total_schemes <= 1 or not scheme or scheme == SINGLE_SCHEME:
        return ""
    if scheme.startswith(_SCHEME_PREFIX):
        return f"评分方案{scheme[len(_SCHEME_PREFIX):]}"
    return str(scheme)


@dataclass
class SchemeSegmenter:
    """评分方案切分状态机(按 chunk 处理顺序驱动)。

 C3: 维护 _boundary_scheme_map（boundary_id → scheme），
 A→B→A 回访时从 map 中查找并复用 A 的 scheme，不新建 scheme_3。
 C4: 结构边界分支前先执行同指纹副本检查；同指纹 A→B 不开新 scheme。
 C6: 维护 _fingerprints_by_scheme（scheme_index → fingerprint_set），
 副本检查只查当前/目标 scheme 的指纹集合，A→B→A 回访后 B(fpB新chunk)仍 scheme_2。
 """

    current_scheme_index: int = 1
    _fingerprints_by_scheme: dict[int, set[str]] = field(default_factory=dict)  # C6
    _scheme_count: int = 1
    _current_boundary_id: str | None = field(default=None)
    _boundary_scheme_map: dict[str, str] = field(default_factory=dict)  # C3

    @property
    def current_scheme(self) -> str:
        return f"{_SCHEME_PREFIX}{self.current_scheme_index}"

    @property
    def scheme_count(self) -> int:
        return self._scheme_count

    def _scheme_fingerprints(self, index: int) -> set:
        """返回指定 scheme index 的指纹集合（C6，按需创建）。"""
        return self._fingerprints_by_scheme.setdefault(index, set())

    @property
    def _current_fingerprints(self) -> set:
        """当前 scheme 的指纹集合（C6）。"""
        return self._scheme_fingerprints(self.current_scheme_index)

    def assign_scheme_for_chunk(
        self,
        *,
        chunk_fingerprint: str | None,
        incoming_item_count: int,
        current_survivors: list[Any],
        volume_defs: dict[str, Any],
        scheme_boundary_id: str | None = None,
    ) -> str:
        """判定 incoming chunk 的 scheme 归属并推进状态机, 返回 scheme 标识。

 C4 优先: 同指纹副本检查先于结构边界检查（查当前 scheme 指纹集）。
 C3: boundary_id 已见过 → 从 map 中复用其 scheme（A→B→A revisit）。
 C6: 副本检查只查目标/当前 scheme 的指纹集合（per-scheme fingerprint set）。
 新可信结构边界（非副本、非回访、与当前不同）→ 立即推进。
 """
        if incoming_item_count <= 0:
            return self.current_scheme

        # C4: 指纹副本检查优先——同指纹 chunk 即使来自新边界也视为副本，不推进
        # C6: 只查当前 scheme 的指纹集合，不跨 scheme 比较
        is_copy = bool(chunk_fingerprint) and chunk_fingerprint in self._current_fingerprints
        if is_copy:
            return self.current_scheme

        is_new_boundary = (
            scheme_boundary_id is not None
            and scheme_boundary_id != self._current_boundary_id
        )

        if is_new_boundary:
            # C3: 回访已见过的边界 → 复用该边界对应的历史 scheme
            if scheme_boundary_id in self._boundary_scheme_map:
                old_scheme = self._boundary_scheme_map[scheme_boundary_id]
                self._current_boundary_id = scheme_boundary_id
                old_index = int(old_scheme[len(_SCHEME_PREFIX):])
                self.current_scheme_index = old_index
                # C6: 将指纹加入复用的 scheme 集合（非副本，新内容）
                if chunk_fingerprint:
                    self._scheme_fingerprints(old_index).add(chunk_fingerprint)
                return self.current_scheme

            # 全新边界且有既有事实 → 推进方案
            if self._has_current_facts(current_survivors):
                self._register_current_boundary()
                self.current_scheme_index += 1
                self._scheme_count += 1
                # C6: 新 scheme 从 _fingerprints_by_scheme 中自动获取新空集

            self._current_boundary_id = scheme_boundary_id
            self._boundary_scheme_map[scheme_boundary_id] = self.current_scheme
            if chunk_fingerprint:
                self._current_fingerprints.add(chunk_fingerprint)
            return self.current_scheme

        # 无结构边界时保持旧逻辑（闭合 + 新指纹才推进）
        if (
            self._has_current_facts(current_survivors)
            and self._current_scheme_closed(current_survivors, volume_defs)
        ):
            if self._current_boundary_id is not None:
                self._register_current_boundary()
            self.current_scheme_index += 1
            self._scheme_count += 1
            # C6: 新 scheme 从 _fingerprints_by_scheme 中自动获取新空集
            self._current_boundary_id = None
        if chunk_fingerprint:
            self._current_fingerprints.add(chunk_fingerprint)
        return self.current_scheme

    def _register_current_boundary(self) -> None:
        """将当前 boundary_id 与当前 scheme 的映射持久化（C3 辅助）。"""
        if (
            self._current_boundary_id is not None
            and self._current_boundary_id not in self._boundary_scheme_map
        ):
            self._boundary_scheme_map[self._current_boundary_id] = self.current_scheme

    def _has_current_facts(self, survivors: list[Any]) -> bool:
        return any(
            getattr(item, "scheme", None) == self.current_scheme for item in survivors
        )

    def _current_scheme_closed(
        self, survivors: list[Any], volume_defs: dict[str, Any]
    ) -> bool:
        """当前方案是否已闭合: 单一百分制合计≈100, 或各卷合计均=卷基准(加权制)。

 前置否决(伴生误切分, 项目1700): 已声明卷基准却零事实的卷存在时,
 方案必未闭合——框架表合成的价格卷100分事实自身凑满100, 但同表声明的技术卷
 (40%权重)明细还在后页, 此时切新方案会把同一评审办法拆成两套方案。
 判定与 scoring_scope_validator 共用 declared_volumes_without_coverage 单源。
 """
        from app.core.extractor.scoring.volume_parser import (
            declared_volumes_without_coverage,
        )

        current = [
            item for item in survivors
            if getattr(item, "scheme", None) == self.current_scheme
        ]
        if not current:
            return False
        covered = {
            str(getattr(item, "volume", "") or "unspecified") for item in current
        }
        if declared_volumes_without_coverage(covered, volume_defs):
            return False
        total = round(sum(float(getattr(i, "score_weight", 0) or 0) for i in current), 4)
        if abs(total - _EXPECTED_TOTAL) <= _SCHEME_CLOSURE_TOLERANCE:
            return True
        if not volume_defs:
            return False
        by_volume: dict[str, float] = {}
        for item in current:
            volume = str(getattr(item, "volume", "") or "unspecified")
            by_volume[volume] = round(
                by_volume.get(volume, 0.0) + float(getattr(item, "score_weight", 0) or 0), 4
            )
        checks = []
        for volume, vol_sum in by_volume.items():
            vdef = volume_defs.get(volume)
            basis = float(getattr(vdef, "volume_max", 0) or 0) if vdef is not None else 0.0
            if basis <= 0:
                return False
            checks.append(abs(vol_sum - basis) <= _SCHEME_CLOSURE_TOLERANCE)
        return bool(checks) and all(checks)


def stamp_items_scheme(items: list[Any], scheme: str) -> None:
    """就地给评分项盖 scheme 标签(缺字段的对象静默跳过)。"""
    for item in items:
        try:
            item.scheme = scheme
        except (AttributeError, ValueError):
            pass


__all__ = [
    "SINGLE_SCHEME",
    "SchemeSegmenter",
    "scheme_display_label",
    "stamp_items_scheme",
]
