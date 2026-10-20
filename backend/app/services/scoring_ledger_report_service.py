"""确认页账本对账报告构建服务（换轴重构 P5-1）。

职责: 按文档实时组装账本输入（scoring_table chunks + 评分全集行），运行捕获与
      对账，产出确认页可消费的 JSON 化账本报告（三态/基准层级/组行/多方案信号）。
不做: 不落库、不裁决 legacy needs_review、不调用 LLM、不修改任何评分行。
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document


def _rule_list(meta: dict[str, Any], key: str) -> list[str]:
    """从 scoring_metadata 取规则列表，兼容单字符串与 None（落库形态不统一）。"""
    raw = meta.get(key)
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item or "").strip()]
    return [str(raw)] if str(raw or "").strip() else []


def _ledger_entry_views(requirements: list[Any]) -> list["_LedgerEntryView"]:
    """评分全集行 -> 账本条目视图（名称/分值/原文组标签/规则，与提取侧口径一致）。: 规则字段必须如实带出——此前视图把 score_rules 硬编码为空列表，
 使确认页口径下所有条目都被判"无规则文本"，与提取侧观测结论不一致。
 """
    views: list[_LedgerEntryView] = []
    for req in requirements:
        kind = getattr(req, "requirement_kind", "normal")
        is_key = bool(getattr(req, "is_key_review", 0))
        weight = getattr(req, "score_weight", None)
        try:
            weight_value = float(weight) if weight is not None else 0.0
        except (TypeError, ValueError):
            weight_value = 0.0
        if kind != "scoring_item" and not (is_key and weight_value > 0):
            continue
        meta = getattr(req, "scoring_metadata", None) or {}
        name = str(meta.get("score_item_name") or getattr(req, "description", "") or "")[:80]
        label = str(meta.get("volume_label") or "")
        views.append(
            _LedgerEntryView(
                name,
                weight_value,
                label,
                scheme=str(meta.get("scheme") or "") or None,
                lot=str(meta.get("lot") or "") or None,
                score_rules=_rule_list(meta, "score_rules"),
                deduction_rules=_rule_list(meta, "deduction_rules"),
            )
        )
    return views


def _serialize_leaf_integrity(li: Any) -> dict[str, Any]:
    """LeafIntegrityEvidence -> JSON 子结构（Step2）。旧默认安全。"""
    if li is None:
        return {"verified": False, "basis": "", "inventory_count": 0, "matched_count": 0, "total": 0.0}
    return {
        "verified": bool(getattr(li, "verified", False)),
        "basis": str(getattr(li, "basis", "") or ""),
        "inventory_count": int(getattr(li, "inventory_count", 0)),
        "matched_count": int(getattr(li, "matched_count", 0)),
        "total": float(getattr(li, "total", 0.0)),
    }


def _serialize_scoped_report(sr: Any) -> dict[str, Any]:
    """ScopedReport -> JSON 子结构（供 scope_reports 列表成员使用）。

    I1: scope 为 tuple[str, str]，用索引访问 scope[0]/scope[1]。
    Minor: expected_by_volume 序列化为对象数组 [{"volume": ..., "expected": ...}]。
    I4/I5: 携带 basis_low_confidence/diagnostics/unlabeled_sum/
           insufficiency_flags/multi_scheme_signal 可审计字段。
    """
    # I1: ScopeIdentity = tuple[str, str]，索引访问
    scope_scheme = sr.scope[0]
    scope_lot = sr.scope[1]
    return {
        "scope_id": sr.scope_id,
        "scheme": scope_scheme,
        "lot": scope_lot,
        "status": sr.status.value,
        "basis": sr.basis.value,
        "expected_total": sr.expected_total,
        "extracted_total": sr.extracted_total,
        "total_gap": sr.total_gap,
        # Minor: 对象数组格式，方便前端按 volume 名索引
        "expected_by_volume": [
            {"volume": v, "expected": e} for v, e in (sr.expected_by_volume or ())
        ],
        "applicability": sr.applicability,
        # I4/I5: 可审计字段
        "basis_low_confidence": bool(getattr(sr, "basis_low_confidence", False)),
        "diagnostics": list(getattr(sr, "diagnostics", ())),
        "unlabeled_sum": float(getattr(sr, "unlabeled_sum", 0.0)),
        "insufficiency_count": len(getattr(sr, "insufficiency_flags", ())),
        "multi_scheme_signal": str(getattr(sr, "multi_scheme_signal", "") or ""),
        # Step2: 叶子完整性嵌套对象
        "leaf_integrity": _serialize_leaf_integrity(getattr(sr, "leaf_integrity", None)),
    }


def _serialize_report(report: Any) -> dict[str, Any]:
    """LedgerReport -> 前端可消费 JSON 结构（枚举取 value, 分值保留原精度）。

 Task6 加法扩展：新增 scope_reports 与 global_comparison_allowed 字段；
 旧单 scope 调用方的既有字段语义不变。
 """
    return {
        "status": report.status.value,
        "basis_level": report.basis_level.value,
        "basis_low_confidence": bool(report.basis_low_confidence),
        "expected_total": report.expected_total,
        "extracted_total": report.extracted_total,
        # 折算制下与 expected_total 同轴的提取合计; 非折算制为 None,
        # 前端据此决定是否展示"原始满分合计 X 分, 按权重折算 Y 分"。
        "extracted_total_folded": report.extracted_total_folded,
        "total_gap": report.total_gap,
        "group_rows": [
            {
                "label": row.label,
                "declared": row.declared,
                "extracted_sum": row.extracted_sum,
                "gap": row.gap,
            }
            for row in report.group_rows
        ],
        "unlabeled_sum": report.unlabeled_sum,
        "multi_scheme_signal": report.multi_scheme_signal,
        "insufficiency_count": len(report.insufficiency_flags),
        "inferred_group_label": report.inferred_group_label or None,
        "inferred_group_sum": report.inferred_group_sum,
        "inferred_group_count": report.inferred_group_count,
        "diagnostics": list(report.diagnostics),
        # Task6 加法字段（旧报告默认为 [] / True）
        "scope_reports": [
            _serialize_scoped_report(sr) for sr in (report.scope_reports or ())
        ],
        "global_comparison_allowed": bool(
            getattr(report, "global_comparison_allowed", True)
        ),
        # Step2: 叶子完整性嵌套对象
        "leaf_integrity": _serialize_leaf_integrity(getattr(report, "leaf_integrity", None)),
    }


async def build_scoring_ledger_report(
    db: AsyncSession,
    document_id: int,
    requirements: list[Any],
) -> tuple[dict[str, Any] | None, Any | None]:
    """构建文档级账本对账报告; 返回 (序列化载荷, 核心 LedgerReport)。

 无评分全集时返回 (None, None)（不渲染）；无评分chunk仍以评分项与
 scoring_signals 构建账本，保持确认页与提取侧输入合同一致。
 核心报告供 scoring_verdict 判绿消费（verdict 单源），避免
 verdict 从序列化 JSON 反推枚举。

 异常兜底: 报告为观测性产物, 构建失败只告警返回 (None, None),
 不影响确认页主数据。
 """
    from app.core.extractor.scoring.ledger.declaration_capture import (
        declaration_grids_from_signals,
        declaration_pages_from_signals,
    )
    from app.core.extractor.scoring.ledger.extractor_bridge import (
        observe_scoring_ledger_shadow,
    )

    try:
        items = _ledger_entry_views(requirements)
        if not items:
            return None, None
        result = await db.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id, Chunk.chunk_type == "scoring_table")
            .order_by(Chunk.chunk_index)
        )
        chunks = list(result.scalars().all())
        # 输入范围缺口: 文档级卷声明("满分均为100…分别占30%、15%、55%的权重")
        # 写在正文段落, scoring_table chunk 内没有; 它已被解析期存入 scoring_signals,
        # 此处补喂给账本。只贡献组声明对、不贡献总分候选(见 capture 层注释)。
        signals = (
            await db.execute(
                select(Document.scoring_signals).where(Document.id == document_id)
            )
        ).scalar_one_or_none()
        # 复用 shadow 桥（capture+充分性+对账）; item 形态为轻量视图对象
        report = observe_scoring_ledger_shadow(
            chunks,
            items,
            document_id=document_id,
            declaration_pages=declaration_pages_from_signals(signals),
            # 装配前声明表行: "分值构成"常写成"标题格+内容格"两格一行,
            # 装配层拆散后 chunk 网格取不到, 只能由快照逐字通道补进(同源缺口)
            declaration_grids=declaration_grids_from_signals(signals),
        )
        if report is None:
            return None, None
        return _serialize_report(report), report
    except Exception as exc:  # noqa: BLE001 观测性报告不得影响确认页主链
        logger.warning("[账本报告] document_id={} 构建失败(降级不渲染): {}", document_id, exc)
        return None, None


class _LedgerEntryView:
    """账本条目轻量视图（鸭子类型适配 extractor_bridge 的 item 读取协议）。: scheme/lot 是账本判定方案身份的唯一作用域维度, 视图缺字段等于让
 确认页口径退化为"按分值猜身份", 必须与提取侧口径逐字段对齐。
 """

    __slots__ = (
        "score_item_name",
        "score_weight",
        "volume_label",
        "scheme",
        "lot",
        "score_rules",
        "deduction_rules",
    )

    def __init__(
        self,
        name: str,
        score: float,
        label: str,
        *,
        scheme: str | None = None,
        lot: str | None = None,
        score_rules: list[str] | None = None,
        deduction_rules: list[str] | None = None,
    ) -> None:
        self.score_item_name = name
        self.score_weight = score
        self.volume_label = label or None
        self.scheme = scheme
        self.lot = lot
        self.score_rules: list[str] = list(score_rules or [])
        self.deduction_rules: list[str] = list(deduction_rules or [])


__all__ = ["build_scoring_ledger_report"]
