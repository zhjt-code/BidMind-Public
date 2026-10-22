"""Chunk 证据过滤——统一 scope 归属判断（Task6 结构整改）。

职责: 提供单源的 `chunk_belongs_to_scope` 谓词，以及基于它的
 page/grid 过滤适配器，消灭 scoped_reconciliation 中的两套平行逻辑。
不做: 不做对账、不捕获声明、不调用 LLM/DB、不修改条目数据。

设计约束（复杂度护栏）:
 - 参数 <= 6，嵌套层级 <= 4。
 - ScopeFilterContext: frozen dataclass 汇集所有过滤上下文，单次计算、复用。
 - chunk_belongs_to_scope: 纯函数，单一真值返回 + 可选诊断消息，无副作用。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.extractor.scoring.ledger.contracts import ScopeIdentity
from app.core.extractor.scoring.scoring_facts import ScoringScopeKey

_SINGLE_LOT = ScoringScopeKey.SINGLE_LOT      # "__single__"
_SINGLE_SCHEME = ScoringScopeKey.SINGLE_SCHEME  # "__single__"


@dataclass(frozen=True)
class ScopeFilterContext:
    """单次过滤操作的上下文（不可变，由 coordinator 一次构建并复用）。

    target_scope: 目标 ScopeIdentity = (scheme, lot)。
    lot_has_single_scheme: 该 lot 在所有条目中只有一个 scheme。
    doc_has_single_named_scheme: 文档全量条目中只有一个具名（非 __single__）scheme。
    is_single_scope: 文档级只有一个 scope（单 scope 路径，全文可用）。
    """

    target_scope: ScopeIdentity
    lot_has_single_scheme: bool
    doc_has_single_named_scheme: bool
    is_single_scope: bool


def chunk_belongs_to_scope(
    ctx: ScopeFilterContext,
    chunk_scheme: str | None,
    chunk_lot: str | None,
) -> tuple[bool, str]:
    """判断 chunk（chunk_scheme, chunk_lot）是否归属 ctx.target_scope。

    返回 (belongs: bool, diagnostic: str)。
    diagnostic 非空时表示该 chunk 被歧义排除，调用方可收集为诊断信息。

    规则（多 scope 路径）:
    1. 单 scope → 全部归属（全文可用）。
    2. 具名 lot scope（target_lot != __single__）:
       - chunk_lot != target_lot → 不归属。
       - lot 精确匹配时，检查 scheme:
           chunk_scheme == target_scheme → 归属。
           chunk_scheme is None + lot 单 scheme → 归属。
           chunk_scheme is None + lot 多 scheme → 歧义排除 + 诊断。
           chunk_scheme != target_scheme → 不归属。
    3. no-lot scope（target_lot == __single__）:
       - chunk_lot 不为 None → 不归属（有 lot 的 chunk 不消费）。
       - target_scheme == __single__（默认）→ 归属所有无 lot chunk。
       - target_scheme 具名（O2 对称校验）:
           chunk_scheme == target_scheme → 归属。
           chunk_scheme is None + 文档单 named scheme → 归属。
           chunk_scheme is None + 文档多 named scheme → 歧义排除 + 诊断。
           chunk_scheme != target_scheme → 不归属。
    """
    target_scheme, target_lot = ctx.target_scope
    is_no_lot = target_lot == _SINGLE_LOT
    named_no_lot = is_no_lot and target_scheme != _SINGLE_SCHEME

    if ctx.is_single_scope:
        return True, ""

    if not is_no_lot:
        # 具名 lot scope
        if chunk_lot != target_lot:
            return False, ""
        # lot 精确匹配
        if chunk_scheme is None:
            if ctx.lot_has_single_scheme:
                return True, ""
            return False, (
                f"chunk lot={target_lot} 无 scheme，该 lot 有多 scheme，歧义排除"
            )
        if chunk_scheme == target_scheme:
            return True, ""
        return False, ""

    # no-lot scope
    if chunk_lot is not None:
        return False, ""
    if not named_no_lot:
        return True, ""
    # O2: 具名 no-lot scope 对称校验
    if chunk_scheme == target_scheme:
        return True, ""
    if chunk_scheme is None:
        if ctx.doc_has_single_named_scheme:
            return True, ""
        return False, "no-lot chunk 无 scheme，文档多 named scheme，歧义排除"
    return False, ""


def filter_pages_for_scope(
    ctx: ScopeFilterContext,
    pages: list[tuple[int, str]],
    chunk_scopes: list[tuple[str | None, str | None]],
) -> tuple[list[tuple[int, str]], list[str]]:
    """过滤 pages 保留归属 ctx.target_scope 的页，附带去重诊断。

    Returns: (filtered_pages, deduplicated_diagnostics)
    诊断去重：同类歧义排除信息聚合为单条摘要（避免指数重复）。
    """
    result: list[tuple[int, str]] = []
    diag_keys: dict[str, list[int]] = {}  # 诊断模板 → 被排除页码列表（去重聚合）

    for (pn, txt), (cs, cl) in zip(pages, chunk_scopes):
        ok, diag = chunk_belongs_to_scope(ctx, cs, cl)
        if ok:
            result.append((pn, txt))
        elif diag:
            # 去重：按诊断模板聚合页码，最后一次性生成摘要
            key = diag.split("，歧义")[0]  # 保留诊断类型，去掉页码变量部分
            diag_keys.setdefault(key, []).append(pn)

    # 聚合生成诊断摘要（最多一条 per 类型，页码先 sorted 再 [:10]）
    merged_diags: list[str] = []
    for key, pns in diag_keys.items():
        pages_str = ",".join(str(p) for p in sorted(pns)[:10])
        suffix = f"（共 {len(pns)} 页: {pages_str}）"
        merged_diags.append(f"{key}，歧义排除{suffix}")

    return result, merged_diags


def filter_grids_for_scope(
    ctx: ScopeFilterContext,
    grids: list[tuple[int, list[list[str]]]],
    grid_scopes: list[tuple[str | None, str | None]],
) -> tuple[list[tuple[int, list[list[str]]]], list[str]]:
    """过滤 grids 保留归属 ctx.target_scope 的网格（与 filter_pages_for_scope 对称去重诊断）。

    Returns: (filtered_grids, deduplicated_diagnostics)
    """
    result: list[tuple[int, list[list[str]]]] = []
    diag_keys: dict[str, list[int]] = {}

    for (pn, grid), (cs, cl) in zip(grids, grid_scopes):
        ok, diag = chunk_belongs_to_scope(ctx, cs, cl)
        if ok:
            result.append((pn, grid))
        elif diag:
            key = diag.split("，歧义")[0]
            diag_keys.setdefault(key, []).append(pn)

    merged_diags: list[str] = []
    for key, pns in diag_keys.items():
        pages_str = ",".join(str(p) for p in sorted(pns)[:10])
        suffix = f"（共 {len(pns)} 页: {pages_str}）"
        merged_diags.append(f"{key}，歧义排除{suffix}")

    return result, merged_diags


__all__ = [
    "ScopeFilterContext",
    "chunk_belongs_to_scope",
    "filter_grids_for_scope",
    "filter_pages_for_scope",
]
