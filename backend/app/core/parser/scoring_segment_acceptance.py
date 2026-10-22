"""装配段 chunk 验收判定。

职责: 提供"chunk 验收成功后才提交段来源表键"的纯函数工具，解耦验收判定与 claimed_global 更新。
不做: 不构建 chunk、不调用 LLM/DB、不修改 segment 内部状态、不访问 API 层。

## 生产主路径

`compute_span_claim(seg_results)` — span 级别 claim 计算，由
`collect_assembled_scoring_chunks` 调用（C2 修复）。

规则（accepted-only）: 遍历 span 内所有段的 (extended_source_keys, chunk) 对，
返回所有「被接受段引用的」物理表键集合（accepted_keys）。
- 共享表（同时被接受段和拒绝段引用）：claim，防止 unclaimed fallback 重复处理已
 接受段数据产生重复评分项；同时触发 logger.warning，说明该表对应的拒绝段
 未进入 fallback，数据可能不完整，需人工核对。
- 仅被拒绝段独占的表：不 claim，留给 collect_unclaimed_table_scoring_chunks fallback 恢复。

## 单段 Helper（非生产主路径）

`apply_segment_claim(chunk, source_keys, claimed_global)` — 逐段验收工具，
仅在单段场景（如单元测试辅助）中使用。多段共用同一物理表时，逐段调用会导致
接受段"覆盖"拒绝段的 claim 决策，必须改用 compute_span_claim。

背景:
 原 collect_assembled_scoring_chunks 在 assemble_scoring_segments 返回后立即全量
 state.claimed_global |= claimed，导致 build_segment_chunk 返回 None（段被拒）时
 物理表键仍已被 claim，collect_unclaimed_table_scoring_chunks fallback 跳过该表，
 评分表不可恢复地丢失。
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def apply_segment_claim(
    chunk: object,
    source_keys: frozenset[tuple[int, int]] | set[tuple[int, int]],
    claimed_global: set[tuple[int, int]],
) -> bool:
    """验收 chunk 并按段提交来源表键（单段 helper，非生产主路径）。

    chunk 为 None 时不提交任何键（被拒段），返回 False；
    chunk 非 None 时将 source_keys 并入 claimed_global，返回 True。

    参数:
        chunk: build_segment_chunk 的返回值（None 表示拒绝）。
        source_keys: 该段实际贡献的物理表 (page, table_index) 键集合。
        claimed_global: 全局已认领集合，本函数原地更新。
    """
    if chunk is None:
        return False
    claimed_global |= source_keys
    return True


def compute_span_claim(
    seg_results: list[tuple[set[tuple[int, int]] | frozenset[tuple[int, int]], object]],
) -> set[tuple[int, int]]:
    """按 span 级别计算 claim 的物理表键（生产主路径）。

 规则（accepted-only）:
 - 有任一接受段引用的物理表：claim（防止 unclaimed fallback 重复产出相同评分项）；
 - 共享表（同时被接受段和拒绝段引用）：claim 并触发 logger.warning，
 告知对应拒绝段未进入 fallback，数据可能不完整，需核对；
 - 仅被拒绝段独占的表：不 claim，留给 fallback 恢复（修复原始漏失 bug）。

 参数:
 seg_results: span 内每段的 (extended_source_keys, chunk) 列表，
 chunk 为 None 表示该段被拒。
 返回:
 set: 可安全提交至 claimed_global 的物理表键集合（= accepted_keys）。
 """
    rejected_keys: set[tuple[int, int]] = set()
    accepted_keys: set[tuple[int, int]] = set()
    for source_keys, chunk in seg_results:
        if chunk is None:
            rejected_keys |= source_keys
        else:
            accepted_keys |= source_keys

    shared = accepted_keys & rejected_keys
    if shared:
        logger.warning(
            "物理表部分段验收失败: keys={} 已 claim 防重复，但对应拒绝段未进入 fallback，需核对",
            sorted(shared),
        )

    return accepted_keys


def build_partial_rejection_diagnostics(
    span_results: list[tuple[set[tuple[int, int]] | frozenset[tuple[int, int]], object]],
    seg_chunks: list[tuple[Any, object]],
) -> list[str]:
    """为同 span 内共享物理表的被拒段构建解析诊断信息。

 仅对 scored_row_count > 0 的被拒段产生诊断，避免对空段/dedup 假阳。
 诊断串不含评分规则正文，稳定信号为「物理表部分段验收失败」。

 参数:
 span_results: span 内每段的 (extended_source_keys, chunk) 列表。
 seg_chunks: 与 span_results 对应的 (segment, chunk) 对，供访问 scored_row_count 等属性。
 返回:
 list[str]: 结构化诊断串（无正文），空列表表示无共享拒绝场景。
 """
    accepted_keys: set[tuple[int, int]] = set()
    rejected_keys: set[tuple[int, int]] = set()
    for keys, chunk in span_results:
        if chunk is None:
            rejected_keys |= keys
        else:
            accepted_keys |= keys

    shared = accepted_keys & rejected_keys
    if not shared:
        return []

    diags: list[str] = []
    for seg, chunk in seg_chunks:
        if chunk is not None:
            continue
        seg_keys: set[tuple[int, int]] = set(getattr(seg, "source_keys", ()) or ())
        if not (seg_keys & shared):
            continue
        scored = int(getattr(seg, "scored_row_count", 0) or 0)
        if scored <= 0:
            continue  # 空段/dedup，不产生诊断
        p_start = getattr(seg, "page_start", "?")
        p_end = getattr(seg, "page_end", "?")
        diags.append(
            f"物理表部分段验收失败: pages={p_start}-{p_end} scored_rows={scored}"
        )

    return diags


__all__ = ["apply_segment_claim", "compute_span_claim", "build_partial_rejection_diagnostics"]
