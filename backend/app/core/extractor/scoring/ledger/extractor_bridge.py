"""账本 shadow 观测桥（换轴重构 P2-3， Task6 scoped 路由 修订）。

职责: 在生产提取尾部以只读方式组装账本输入（chunk文本+payload网格+提取项三元组），
 运行捕获与对账并输出日志观测；shadow 阶段不改变任何提取行为与落库数据。
 Task6: 多 scope（lot/scheme）时路由到 scoped_reconciliation coordinator，
 使各 lot 独立对账，禁止跨 lot 全局合并比较。

不做: 不裁决 needs_review（P5 切换前由 legacy 语义承担）、不调用 LLM/DB、
 不修改 items、不抛出异常中断提取（内部兜底, 仅告警）。

I2 修订: _resolve_chunk_lot 私有副本已删除，统一调用 declaration_scope.resolve_chunk_lot。
C3 修订: _scoped_chunk_inputs 返回 per-chunk scope 列表（chunk_scopes），
 供 coordinator 执行 scheme×lot 精确过滤，不再用 page-only dict。
I3 修订: 同页多 lot 按 chunk 顺序各自保留，不做 page_num 后写覆盖。

兼容约定: _chunk_inputs 保留旧 3 元组接口（pages, grids, parallel_labels）供既有
 测试/消费者使用；生产路径（observe_scoring_ledger_shadow）改用
 _scoped_chunk_inputs（5 元组，含 per-chunk scope 信息）。
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.core.extractor.scoring.ledger.contracts import LedgerItemEntry, LedgerReport
from app.core.extractor.scoring.ledger.declaration_scope import (
    resolve_chunk_lot,  # I2: 单源，删除 bridge 私有副本
)


def _chunk_inputs(
    chunks: list[Any],
) -> tuple[
    list[tuple[int, str]],          # pages: (page_num, text)
    list[tuple[int, list[list[str]]]],  # grids: (page_num, grid)
    list[str],                       # parallel_scope_labels
]:
    """旧 3 元组接口（向后兼容，供既有测试/消费者使用）。

    返回 (pages, grids, parallel_scope_labels)。
    per-chunk scope 信息（chunk_scopes/grid_scopes）通过 _scoped_chunk_inputs 获取。
    """
    pages, _chunk_scopes, grids, _grid_scopes, parallel_labels = _scoped_chunk_inputs(chunks)
    return pages, grids, parallel_labels


def _scoped_chunk_inputs(
    chunks: list[Any],
) -> tuple[
    list[tuple[int, str]],                    # pages: (page_num, text) per chunk
    list[tuple[str | None, str | None]],      # chunk_scopes: (scheme, lot) per chunk
    list[tuple[int, list[list[str]]]],        # grids: (page_num, grid) for chunks with grids
    list[tuple[str | None, str | None]],      # grid_scopes: (scheme, lot) for grid chunks
    list[str],                                 # parallel_scope_labels
]:
    """从 scoring_table chunk 组装页文本、网格、per-chunk scope 信息与百分制标签（5 元组）。

    C3/I3 修订:
    - chunk_scopes[i] 与 pages[i] 一一对应，每个 chunk 独立携带 (scheme, lot)。
    - 同页多 lot（如 page 5 承载 lot_A 和 lot_B 两个 chunk）各自保留，
      不用 page_num dict 后写覆盖（I3 fix）。
    - scheme 来自 payload.scheme（Task5 已写入），lot 来自 payload.lot 或 markdown 头。
    """
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk

    pages: list[tuple[int, str]] = []
    chunk_scopes: list[tuple[str | None, str | None]] = []
    grids: list[tuple[int, list[list[str]]]] = []
    grid_scopes: list[tuple[str | None, str | None]] = []
    parallel_scope_labels: list[str] = []

    for chunk in chunks:
        page_num = int(getattr(chunk, "page_num", 0) or 0)
        content = str(getattr(chunk, "content", None) or "")
        payload = payload_from_chunk(chunk)

        # C3: 保留 payload.scheme（Task5 已写入）和 lot（payload 优先，其次 markdown 头）
        raw_scheme = (payload or {}).get("scheme") or None
        payload_lot = (payload or {}).get("lot") or None
        lot = resolve_chunk_lot(content, payload_lot)  # I2: 单源 helper

        # 归一化: 空字符串视为 None（避免 "" 与 None 行为不一致）
        chunk_scheme: str | None = str(raw_scheme).strip() or None if raw_scheme else None
        chunk_lot: str | None = lot.strip() if lot else None

        pages.append((page_num, content))
        chunk_scopes.append((chunk_scheme, chunk_lot))

        grid = (payload or {}).get("grid")
        if grid:
            grids.append((page_num, [[str(c or "") for c in row] for row in grid]))
            grid_scopes.append((chunk_scheme, chunk_lot))

        for scope in (payload or {}).get("scope_rows") or []:
            if not isinstance(scope, dict):
                continue
            try:
                declared = float(scope.get("declared") or 0.0)
            except (TypeError, ValueError):
                continue
            label = str(scope.get("label") or "").strip()
            if label and abs(declared - 100.0) <= 1.0:
                parallel_scope_labels.append(label)

    return pages, chunk_scopes, grids, grid_scopes, parallel_scope_labels


def _ledger_entry_with_rule(item: Any) -> tuple[LedgerItemEntry, str]:
    """提取项 -> (账本条目, 规则文本) 对。

    Minor: 与 rule_text 一起打包，避免后续用 id() 脆弱查表。
    """
    entry = LedgerItemEntry(
        name=str(getattr(item, "score_item_name", "") or ""),
        score=float(getattr(item, "score_weight", 0) or 0.0),
        volume_label=str(getattr(item, "volume_label", None) or ""),
        scheme=str(getattr(item, "scheme", None) or "") or None,
        lot=str(getattr(item, "lot", None) or "") or None,
    )
    rules = [
        *(getattr(item, "score_rules", None) or []),
        *(getattr(item, "deduction_rules", None) or []),
    ]
    rule_text = " ".join(str(rule) for rule in rules if str(rule or "").strip())
    return entry, rule_text


def _leaf_coverage_reasons(
    chunks: list[Any], entries: list[tuple[LedgerItemEntry, str]]
) -> tuple[str, ...]:
    """: 网格叶子覆盖对账——网格有分值行未被任何提取项覆盖即强制复核。

 「逐项提全」原则的确定性落地: 组合计守恒不等于逐项展开, 覆盖基准取自
 payload 网格(经续行缝合); 无网格/无叶子信号时不断言(fail-closed 宁漏不误报)。
 """
    from app.core.extractor.scoring.leaf_inventory import (
        leaf_inventory_from_chunks,
        missing_leaf_names,
    )

    inventory = leaf_inventory_from_chunks(chunks)
    if not inventory:
        return ()
    missing = missing_leaf_names(inventory, [entry.name for entry, _ in entries])
    if not missing:
        return ()
    preview = "、".join(missing[:8])
    return (f"叶子未逐项展开(网格有分值行未提取{len(missing)}条): {preview}",)


def _group_echo_exempt_keys(chunks: list[Any]) -> tuple[str, ...]:
    """: 网格中提不出叶子的组归一键(组行即计分行, 组头自证检测豁免)。

 判据取自 unresolved_group_rule_rows(分值藏规则长句、清单 fail-closed 的组);
 有清单叶子的组不会出现在此集合, 父组冒充形态仍被自证检测拦截。
 """
    from app.core.extractor.scoring.leaf_inventory import unresolved_group_rule_rows
    from app.core.extractor.scoring.ledger.label_normalization import (
        normalize_group_label,
    )
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk

    keys: list[str] = []
    for chunk in chunks or []:
        grid = (payload_from_chunk(chunk) or {}).get("grid")
        if not grid:
            continue
        for group_cell, _rule_cell in unresolved_group_rule_rows(grid):
            key = normalize_group_label(group_cell)
            if key:
                keys.append(key)
    return tuple(dict.fromkeys(keys))


def observe_scoring_ledger_shadow(
    chunks: list[Any],
    items: list[Any],
    *,
    document_id: int,
    declaration_pages: list[tuple[int, str]] | None = None,
    declaration_grids: list[tuple[int, list[list[str]]]] | None = None,
    log_summary: bool = True,
) -> LedgerReport | None:
    """shadow 观测: 运行账本捕获+对账并记录日志; 任何内部异常只告警不中断提取。

 log_summary=False 供 verdict 复算等静默调用使用（: 同一提取内
 二次对账不重复刷 INFO 日志），异常告警不受该开关影响。

 Task6: 自动检测多 scope（不同 lot/scheme 组合），路由到
 scoped_reconciliation coordinator；单 scope 保持原有标量语义（backward compat）。

 declaration_pages: 文档级声明补充文本。多 scope 时不广播（I6: 追加诊断，不静默）。
 chunk 正文里没有的卷声明只能由此进入账本。

 declaration_grids: 装配前捕获的声明样表行（见 declaration_grids_from_signals）。
 这类"标题格+内容格"两格一行的分值构成声明在装配层会被拆散，chunk 网格里取不到；
 与 declaration_pages 同为无 scope 归属证据，多 scope 时同样不广播。
 """
    from app.core.extractor.scoring.ledger.scoped_contracts import SINGLE_LOT_CONST
    from app.core.extractor.scoring.ledger.scoped_reconciliation import (
        ScopedLedgerRequest,
        coordinate_scoped_ledger,
    )

    try:
        pages, chunk_scopes, grids, grid_scopes, parallel_scope_labels = _scoped_chunk_inputs(
            chunks
        )
        # Minor: entries_with_rules 打包，coordinator 直接分区，避免 id() 查表
        entries_with_rules: list[tuple[LedgerItemEntry, str]] = [
            _ledger_entry_with_rule(item) for item in items
        ]

        # 检测有效 scope 数量（仅当存在 >=2 个不同 lot 时进入多 scope 路径）
        # O6: 用 SINGLE_LOT_CONST（lot 缺省语义），不用 scheme 别名 SINGLE_SCOPE 比较 lot
        lots = {e.lot for e, _ in entries_with_rules if e.lot and e.lot != SINGLE_LOT_CONST}
        is_multi_scope = len(lots) >= 2

        # 多 scope 下叶子清单无 scope 归属证据, 与 declaration_* 同源政策跳过
        leaf_reasons = (
            () if is_multi_scope else _leaf_coverage_reasons(chunks, entries_with_rules)
        )
        # 无叶子组豁免键(与叶子清单同源自网格, 多 scope 同政策跳过)
        exempt_keys = () if is_multi_scope else _group_echo_exempt_keys(chunks)

        request = ScopedLedgerRequest(
            pages=tuple(pages),
            chunk_scopes=tuple(chunk_scopes),
            grids=tuple(grids),
            grid_scopes=tuple(grid_scopes),
            entries_with_rules=tuple(entries_with_rules),
            declaration_pages=tuple(declaration_pages or []),
            parallel_scope_labels=tuple(parallel_scope_labels),
            document_id=document_id,
            declaration_grids=tuple(declaration_grids or []),
            extra_review_reasons=leaf_reasons,
            group_echo_exempt_keys=exempt_keys,
        )
        report = coordinate_scoped_ledger(request)

        if not log_summary:
            return report
        if is_multi_scope:
            logger.info(
                "[账本shadow] document_id={} 多scope模式 scopes={} global_ok={} "
                "extracted={} scope_reports={}",
                document_id,
                sorted(lots),
                report.global_comparison_allowed,
                report.extracted_total,
                len(report.scope_reports),
            )
        else:
            logger.info(
                "[账本shadow] document_id={} status={} basis={} low_conf={} expected={} "
                "extracted={} gap={} 组行={} 多方案={} 未归组={}",
                document_id,
                report.status.value,
                report.basis_level.value,
                report.basis_low_confidence,
                report.expected_total,
                report.extracted_total,
                report.total_gap,
                len(report.group_rows),
                bool(report.multi_scheme_signal),
                report.unlabeled_sum,
            )
        return report
    except Exception as exc:  # noqa: BLE001 shadow观测不得中断提取主链
        logger.warning("[账本shadow] document_id={} 观测失败(不影响提取): {}", document_id, exc)
        return None


__all__ = ["observe_scoring_ledger_shadow"]
