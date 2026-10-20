"""候选救援轮次引擎(传输无关, 自 vlm_rescue_channel 原样搬迁)。

职责: 承载"候选提取(注入) -> 卷定义补齐 -> 锚定/闭合门禁 -> verdict 单源复判
 -> 跨轮取优 -> 分级采纳"的完整轮次循环, 供 VLM 视觉通道与 chat-PDF
 直读通道共同消费——门禁/终点/采纳裁决单源, 禁止各通道自行复制。
不做: 不装配输入(渲染/子PDF归通道)、不直接调模型(经注入的 extract_candidates)、
 不判定触发条件、不落库。

搬迁背景(2026-08-15): 该循环沉淀了 系列裁决,
新增 chat-PDF 通道时若复制将产生第二真相源, 故整体抽为共享引擎。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from loguru import logger


@dataclass(frozen=True)
class RescueChannelNotes:
    """通道文案与埋点标识(引擎裁决逻辑与通道无关, 仅文案区分)。"""

    channel: str = "vlm_rescue"
    action_label: str = "视觉救援"
    log_prefix: str = "[评分VLM救援]"
    fidelity_note: str = "VLM视觉救援已通过锚定+闭合双门禁(vlm_rescue)"
    fidelity_note_partial: str = "VLM视觉救援已采纳待人工核对核计(vlm_rescue)"


@dataclass(frozen=True)
class RescueRoundsRequest:
    """轮次引擎入参(护栏: 参数>6 收敛为 dataclass)。"""

    tail: Any
    db: Any
    document_id: int
    scoring_chunks: list
    volume_defs: dict
    anchor_text: str
    max_rounds: int
    meta: dict
    false_green_current: bool = False
    judge_context: Any = None
    notes: RescueChannelNotes = field(default_factory=RescueChannelNotes)
    rescue_pages: tuple[int, ...] = ()
    require_group_relation: bool = False


# 候选提取注入点: (gate_feedback) -> (items, header)
ExtractCandidatesFn = Callable[[str], Awaitable[tuple[list, Optional[dict]]]]


def _scope_gap_metric(items: list, expected_per_lot: float) -> tuple[int, float]:
    """按 (scheme, lot) scope 聚合的质量度量: (未闭合scope数, Σ|scope合计-期望|)。

    审查V3: 单轴 |expected-total| 在多 scope 文档上无意义(两标段合计被塞进
    单包轴), 候选与现状必须在同一 scope 轴上比较; 元组字典序越小越好。
    """
    totals: dict[tuple[str, str], float] = {}
    for item in items or []:
        key = (
            str(getattr(item, "scheme", None) or ""),
            str(getattr(item, "lot", None) or ""),
        )
        totals[key] = totals.get(key, 0.0) + float(getattr(item, "score_weight", 0) or 0.0)
    if not totals:
        return (10**6, float("inf"))
    gaps = [abs(total - expected_per_lot) for total in totals.values()]
    failed = sum(1 for gap in gaps if gap > 1.0)
    return (failed, round(sum(gaps), 4))


def _reattribute_item_volumes(items: list, volume_defs: dict) -> list:
    """header 卷定义补齐后, 给尚未归卷的项补 volume(仅当推断结果落在声明卷中)。"""
    from app.core.extractor.scoring.weighted_volume_table import (
        volume_from_scoring_label,
    )

    attributed: list = []
    for item in items:
        if getattr(item, "volume", None) or not volume_defs:
            attributed.append(item)
            continue
        hint = " ".join(
            part
            for part in (
                str(getattr(item, "volume_label", "") or ""),
                str(getattr(item, "score_item_name", "") or ""),
            )
            if part
        )
        volume = volume_from_scoring_label(hint, default="")
        if volume not in volume_defs:
            attributed.append(item)
            continue
        try:
            attributed.append(item.model_copy(update={"volume": volume}))
        except (AttributeError, ValueError, TypeError):
            attributed.append(item)
    return attributed


def _compact(text: str) -> str:
    """与 candidate_gates._compact 同口径(审查V4: NFKC+去空白+小写)。"""
    import unicodedata

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()


def _volume_defs_from_header(header: Optional[dict], anchor_text: str) -> dict:
    """候选 header.volume_totals 经页文本锚定后转为卷定义; fail-closed 不完整则空。"""
    if not header or not anchor_text:
        return {}
    totals = header.get("volume_totals") or []
    if not isinstance(totals, list) or len(totals) < 2:
        return {}
    from app.core.extractor.scoring.volume_parser import VolumeDefinition
    from app.core.extractor.scoring.weighted_volume_table import (
        volume_from_scoring_label,
    )

    compact_anchor = _compact(anchor_text)
    defs: dict = {}
    for entry in totals:
        if not isinstance(entry, dict):
            return {}
        label = str(entry.get("label") or "").strip()
        if not label or _compact(label) not in compact_anchor:
            return {}
        try:
            total = float(entry.get("total"))
        except (TypeError, ValueError):
            return {}
        if total <= 0:
            return {}
        score_reps = [f"{total:g}"]
        if float(total).is_integer():
            score_reps.insert(0, str(int(total)))
        if not any(rep in compact_anchor for rep in score_reps):
            return {}
        volume = volume_from_scoring_label(label, default="")
        if not volume:
            continue
        if volume in defs:
            return {}
        raw_weight = entry.get("weight")
        try:
            weight = float(raw_weight) if raw_weight is not None else round(total / 100.0, 4)
        except (TypeError, ValueError):
            weight = round(total / 100.0, 4)
        if weight > 1:
            weight = round(weight / 100.0, 4)
        defs[volume] = VolumeDefinition(
            volume=volume,
            weight=weight,
            volume_max=total,
            label=label,
        )
    return defs if len(defs) >= 2 else {}


async def _judge_candidate_verdict(
    *, db: Any, document_id: int, chunks: list[Any], items: list[Any],
    diagnostics: list[str],
) -> tuple[str, Any]:
    """职责: 以候选 items 走确认页同轴的 verdict 单源公式复判(终点)。
 不做: 不实现任何判定谓词、不缓存、不吞异常(通道级 fail-open 兜底)。

 必须经 resolve_document_trigger_context(含 DB 声明信号, 同源教训);
 禁止在此改用无信号版本复算——那等于又造一把新尺子。
 """
    from app.core.extractor.scoring.scoring_green_verdict import (
        resolve_document_trigger_context,
    )

    return await resolve_document_trigger_context(
        db=db, document_id=document_id, chunks=list(chunks),
        items=list(items), diagnostics=list(diagnostics),
    )


def _record_round_candidate(meta: dict, outcome: Any, candidate_verdict: str) -> None:
    """职责: 每轮候选摘要埋点(审查V6, 低基数无原文), rejected 排障免重放取证。
    不做: 不参与裁决、不记录候选原文。
    """
    if outcome.validation is None:
        return
    meta.setdefault("round_candidates", []).append({
        "kind": outcome.validation.kind.value,
        "verdict": candidate_verdict,
        "totals_by_lot": {
            str(k): v
            for k, v in dict(outcome.validation.totals_by_lot or {}).items()
        },
        "failed_scopes": len(outcome.validation.failed_scopes or ()),
    })


def _verdict_gap_feedback(ledger: Any) -> str:
    """职责: 账本分组差额转重询反馈(逐组"要求X提取Y差Z", doc2484 卷错位形态)。
    不做: 不做判定、不改账本、无差额时返回空串。
    """
    if ledger is None:
        return ""
    rows = [
        row for row in (getattr(ledger, "group_rows", None) or ())
        if abs(float(getattr(row, "gap", 0.0) or 0.0)) > 0.01
    ]
    parts = [
        f"{row.label}: 要求{float(row.declared):g}分, "
        f"当前提取{float(row.extracted_sum):g}分(差{float(row.gap):+g}分)"
        for row in rows[:6]
    ]
    unlabeled = float(getattr(ledger, "unlabeled_sum", 0.0) or 0.0)
    if unlabeled > 0.01:
        parts.append(f"另有{unlabeled:g}分未归入任何评分组")
    if not parts:
        return ""
    return (
        "分组核对未通过: " + "; ".join(parts)
        + "。请逐项重新核对评分项所属的评分组/卷(以原文表格分组表头为准), 禁止改动分值。"
    )


def _build_retry_feedback(
    outcome: Any, kept: list, dropped: list, scoring_chunks: list | None = None
) -> str:
    """构造重询反馈: 门禁反馈 + 同名多行守恒提示(父组与子项双计形态)。

 doc2478 实测教训: VLM 把父组行与其子项同时输出为明细(如「央行动态15」
 与两条同名 5 分子行), 核计溢出; 反馈中点名同名行促使下一轮只输出叶子行。: 网格中有分值、候选中无的项名一并点名, 避免只给分组差额。
 """
    feedback = str(getattr(outcome, "feedback", "") or "")
    names = [str(getattr(item, "score_item_name", "") or "") for item in kept or []]
    duplicated = sorted({name for name in names if name and names.count(name) > 1})
    if duplicated:
        preview = "、".join(duplicated[:5])
        feedback = (
            f"{feedback} 检测到同名多行(疑父组与子项重复计分): {preview}; "
            f"父组行禁止与其子项同时计分, 只输出叶子明细行。"
        ).strip()
    from app.core.extractor.scoring.vlm.rescue_missing_items import (
        format_missing_item_feedback,
        group_rule_row_hints,
        missing_scored_item_names,
    )

    missing_hint = format_missing_item_feedback(
        missing_scored_item_names(scoring_chunks or [], kept)
    )
    if missing_hint:
        feedback = f"{feedback} {missing_hint}".strip()
    # 拒收原因必须进反馈, 否则模型无法自我纠正命名(3479 三轮同错)
    if dropped:
        preview = "；".join(str(reason) for reason in dropped[:5])
        feedback = (
            f"{feedback} 以下条目未通过原文锚定校验被拒收: {preview}。"
            f"score_item_name 与规则文字必须逐字取自原文单元格"
            f"(父项名称-子行名称的子行段同样逐字), 禁止概括改写。"
        ).strip()
    # 无清单叶子的组(分值藏在长规则句)点名帮不到, 附计分行原文
    group_hints = group_rule_row_hints(scoring_chunks or [], kept)
    if group_hints:
        feedback = (
            f"{feedback} 以下评分组的计分行漏提, 必须按原文输出为独立评分项: "
            + "；".join(group_hints)
        ).strip()
    return feedback


def _leaf_coverage(items: list, leaf_names: tuple[str, ...]) -> int:
    """候选/现状对网格叶子清单的覆盖数(C 轴决胜口径, 委托 leaf_inventory)。"""
    if not leaf_names:
        return 0
    from app.core.extractor.scoring.leaf_inventory import covered_leaf_count

    return covered_leaf_count(
        leaf_names,
        [str(getattr(item, "score_item_name", "") or "") for item in items or []],
    )


def _resolve_adoption(
    request: RescueRoundsRequest,
    best: tuple[tuple, list, Any, str] | None,
    *,
    compat_expected: float,
    leaf_names: tuple[str, ...] = (),
) -> Any | None:
    """职责: 跨轮最优候选的采纳裁决(verdict绿/闭合直采, 非绿走偏序红线)。
    不做: 不重询模型、不复判 verdict、不修改候选内容。
    """
    from app.core.extractor.scoring.scoring_scope_contracts import ScoringValidationKind

    tail, meta = request.tail, request.meta
    document_id, notes = request.document_id, request.notes
    if best is None:
        meta["status"] = "rejected"
        return None
    _rank, kept, outcome, final_verdict = best
    meta["final_verdict"] = final_verdict
    validation = outcome.validation
    # verdict 绿(终点达成)或闭合 COMPLETE(耗满轮数时的最优候选)直接采纳;
    # partial 语义保持闭合口径(契约), 终态绿与否由 final_verdict 表达。
    if final_verdict == "green" or validation.kind == ScoringValidationKind.COMPLETE:
        return _adopt_candidate(
            tail, kept, outcome, meta=meta, document_id=document_id,
            partial=validation.kind != ScoringValidationKind.COMPLETE,
            notes=notes,
        )
    # 假绿现状的 gap=0 是循环论证的虚假优势(错误项恰凑满声明总分),
    # 不作拒收基准——锚定门禁通过的候选一律部分采纳, 真实缺口经 needs_review 暴露。
    # 但直采必须有确定性质量下限——候选叶子覆盖数低于现状即拒,
    # 防 43 分残缺候选覆盖掉含更多真叶子的现状; 无清单证据时维持 原契约。
    if request.false_green_current:
        meta["false_green_current"] = True
        if leaf_names and _leaf_coverage(kept, leaf_names) < _leaf_coverage(
            list(tail.items or []), leaf_names
        ):
            meta["status"] = "rejected"
            meta["reject_reason"] = "leaf_coverage_regression"
            return None
        return _adopt_candidate(
            tail, kept, outcome, meta=meta, document_id=document_id, partial=True,
            notes=notes,
        )
    # 审查V3: 候选与现状在同一 scope 轴上比较(未闭合scope数优先, 缺口和次之),
    # 多 scope 文档不再被单包轴的无意义 gap 误拒。
    candidate_gap = _scope_gap_metric(kept, compat_expected)
    current_gap = _scope_gap_metric(list(tail.items or []), compat_expected)
    if candidate_gap < current_gap:
        return _adopt_candidate(
            tail, kept, outcome, meta=meta, document_id=document_id, partial=True,
            notes=notes,
        )
    # 守恒打平时叶子覆盖多者胜——等分不是终局地板, 逐项提全优先。
    if candidate_gap == current_gap and _leaf_coverage(kept, leaf_names) > _leaf_coverage(
        list(tail.items or []), leaf_names
    ):
        meta["leaf_coverage_edge"] = True
        return _adopt_candidate(
            tail, kept, outcome, meta=meta, document_id=document_id, partial=True,
            notes=notes,
        )
    meta["status"] = "rejected"
    meta["reject_reason"] = "not_better_than_current"
    return None


def _adopt_candidate(
    tail: Any,
    items: list,
    outcome: Any,
    *,
    meta: dict,
    document_id: int,
    partial: bool,
    notes: RescueChannelNotes,
) -> Any:
    """构造采纳后的新尾部结果(原子替换, 不修改传入 tail 的业务字段)。"""
    from app.core.extractor.scoring.scoring_fidelity import (
        FidelityAnnotation,
        build_fidelity_key,
    )
    from app.core.extractor.scoring.scoring_rescue_integration import (
        ScoringRescueIntegrationResult,
    )

    new_report = outcome.validation.to_score_totals_report()
    sidecar = dict(tail.fidelity_sidecar or {})
    note = notes.fidelity_note_partial if partial else notes.fidelity_note
    for item in items:
        sidecar[build_fidelity_key(item)] = FidelityAnnotation(
            flag="ok", note=note, overlap_score=1.0
        )
    diagnostics = list(tail.diagnostics or [])
    if partial:
        diagnostics.append(
            f"{notes.channel}: {notes.action_label}补录{len(items)}项, "
            f"核计{new_report.total}/{new_report.expected_total}待人工核对"
        )
        meta["status"] = "ok_needs_review"
    else:
        diagnostics.append(
            f"{notes.channel}: {notes.action_label}采纳{len(items)}项, "
            f"总分{new_report.total}"
        )
        meta["status"] = "ok"
    logger.info(
        "{} 采纳成功 doc={}: items={} total={} partial={} final_verdict={}",
        notes.log_prefix, document_id, len(items), new_report.total, partial,
        meta.get("final_verdict", ""),
    )
    return ScoringRescueIntegrationResult(
        items=list(items),
        report=new_report,
        fidelity_sidecar=sidecar,
        diagnostics=diagnostics,
        l3_diagnostics=tail.l3_diagnostics,
        adopted=True,
        mode=tail.mode,
    )


async def _load_group_relation_evidence(request: RescueRoundsRequest) -> list[Any]:
    """零评分chunk路径按实际救援页加载普通表格行，非该路径零查询。"""
    if not request.require_group_relation:
        return []
    from app.core.extractor.scoring.group_relation_loader import (
        load_group_relation_rows,
    )

    return await load_group_relation_rows(
        request.db,
        request.document_id,
        request.rescue_pages,
    )


def _apply_group_relation_authority(
    request: RescueRoundsRequest,
    items: list[Any],
    relation_rows: list[Any],
) -> tuple[list[Any], bool, dict[str, float]]:
    """应用结构关系裁判并记录低基数轮次摘要，不在轮次主体堆解析职责。"""
    from app.core.extractor.scoring.item_group_relation import (
        reconcile_item_group_relations,
    )

    outcome = reconcile_item_group_relations(
        items,
        relation_rows,
        require_authority=request.require_group_relation,
    )
    if request.require_group_relation or outcome.authoritative:
        request.meta.setdefault("group_relation_rounds", []).append(
            {
                "authoritative": outcome.authoritative,
                "corrected": outcome.corrected_count,
                "cleared": outcome.cleared_count,
                "reason": outcome.reason,
            }
        )
    return list(outcome.items), outcome.authoritative, dict(outcome.group_totals)


async def run_candidate_rescue_rounds(
    request: RescueRoundsRequest,
    extract_candidates: ExtractCandidatesFn,
) -> Any | None:
    """执行候选救援轮次循环; 采纳返回新尾部结果, 未采纳返回 None。

 阶段1 重试增强 + 终点单源: 候选未经 verdict 复判转绿不定局——
 携缺口反馈重询(轮数上限可配), 跨轮按 (verdict绿, 完整闭合, scope轴质量)
 取优, 对冲模型 run-to-run 方差; 耗满轮数未绿采最优并如实记录 final_verdict。
 /审查V1: 多 scope(lot/scheme)候选经 chunk 页映射确定性回填。
 """
    from app.core.extractor.scoring.physical_page_remap import (
        collect_chunk_physical_pages,
        remap_item_source_pages,
    )
    from app.core.extractor.scoring.scoring_scope_contracts import ScoringValidationKind
    from app.core.extractor.scoring.vlm.candidate_gates import (
        evaluate_candidate_gates,
        filter_anchored_items,
        sanitize_unanchored_group_labels,
    )
    from app.core.extractor.scoring.vlm.scope_context import (
        backfill_item_scopes_from_chunks,
    )
    from app.core.extractor.scoring.volume_parser import apply_volume_definitions

    tail = request.tail
    meta = request.meta
    judge_context = request.judge_context
    # 审查V2: 账本高置信文档级基准; None 时回退默认 100(百分制行业默认)。
    # 有权威基准(如150分制)必须传递, 否则"漏提凑满默认100"会被误判 COMPLETE。
    authoritative_expected = (
        judge_context.authoritative_expected_total if judge_context is not None else None
    )
    compat_expected = (
        authoritative_expected if authoritative_expected is not None else 100.0
    )
    # 网格叶子清单一次装配——名称轴回退锚定源(E) + 采纳覆盖决胜(C)
    from app.core.extractor.scoring.leaf_inventory import leaf_inventory_from_chunks

    inventory_names = tuple(
        name for name, _ in leaf_inventory_from_chunks(list(request.scoring_chunks or []))
    )
    relation_rows = await _load_group_relation_evidence(request)
    gate_feedback = ""
    best: tuple[tuple, list, Any, str] | None = None
    max_rounds = max(1, request.max_rounds)
    for round_no in range(max_rounds):
        items, header = await extract_candidates(gate_feedback)
        items = remap_item_source_pages(
            items,
            collect_chunk_physical_pages(list(request.scoring_chunks or [])),
        )
        items = backfill_item_scopes_from_chunks(
            items, list(request.scoring_chunks or [])
        )
        # 分组标签轴锚定净化必须先于卷推断——伪造标签(如照抄示例的
        # "技术卷")此前原样透传落库, 使 unlabeled_sum=0 驱动假绿。
        items, stripped_labels = sanitize_unanchored_group_labels(
            items, request.anchor_text
        )
        if stripped_labels:
            meta.setdefault("stripped_group_labels", []).append(len(stripped_labels))
        items, group_relation_authoritative, relation_group_totals = (
            _apply_group_relation_authority(request, items, relation_rows)
        )
        effective_defs = dict(request.volume_defs or {})
        if not effective_defs:
            # 权威关系存在时卷定义只认结构闭合分值（含跨页缝合的组总分），
            # 模型 header 自述总分整轴旁路——弱子串锚定放行过谎报的跨页组总分。
            if group_relation_authoritative:
                from app.core.extractor.scoring.item_group_relation import (
                    volume_defs_from_group_totals,
                )

                relation_defs = volume_defs_from_group_totals(relation_group_totals)
                if relation_defs:
                    effective_defs = relation_defs
                    meta["volume_defs_from_relation"] = True
            else:
                fallback_defs = _volume_defs_from_header(header, request.anchor_text)
                if fallback_defs:
                    effective_defs = fallback_defs
                    meta["volume_defs_from_header"] = True
        if not request.require_group_relation or group_relation_authoritative:
            if effective_defs:
                items = _reattribute_item_volumes(items, effective_defs)
            apply_volume_definitions(items, effective_defs)
        kept, dropped = filter_anchored_items(
            items, request.anchor_text, fallback_names=inventory_names
        )
        outcome = evaluate_candidate_gates(
            kept if kept else items,
            anchor_text=request.anchor_text,
            volume_defs=effective_defs,
            document_id=request.document_id,
            expected_per_lot=authoritative_expected,
        )
        if dropped:
            meta.setdefault("dropped_unanchored", []).append(len(dropped))
        candidate_verdict = ""
        candidate_ledger: Any = None
        if outcome.passed and kept and outcome.validation is not None:
            # 终点单源: 候选是否"通过"由 verdict 公式裁决(与确认页横幅
            # 同轴, 含账本分组对账)——总分守恒的卷间错位对闭合总分轴不可见,
            # 闭合门禁降级为分级采纳输入, 不再拥有定局权。
            candidate_verdict, candidate_ledger = await _judge_candidate_verdict(
                db=request.db, document_id=request.document_id,
                chunks=list(request.scoring_chunks or []), items=kept,
                diagnostics=list(tail.diagnostics or []),
            )
            candidate_complete = (
                outcome.validation.kind == ScoringValidationKind.COMPLETE
            )
            gap_metric = _scope_gap_metric(kept, compat_expected)
            # 叶子覆盖数入 rank——同守恒质量下逐项展开更全者跨轮胜出
            rank = (
                1 if candidate_verdict == "green" else 0,
                1 if candidate_complete else 0,
                _leaf_coverage(kept, inventory_names),
                -gap_metric[0],
                -gap_metric[1],
            )
            if best is None or rank > best[0]:
                best = (rank, kept, outcome, candidate_verdict)
        _record_round_candidate(meta, outcome, candidate_verdict)
        if candidate_verdict == "green":
            break  # verdict 终点: 与确认页同轴转绿才定局(用户裁决 green-only)
        rejection_codes = list(outcome.rejection_codes)
        if not rejection_codes:
            rejection_codes = (
                [f"verdict_{candidate_verdict}"] if candidate_verdict else ["unanchored"]
            )
        meta.setdefault("gate_rejections", []).append(rejection_codes)
        logger.info(
            "{} 未达verdict终点(round={}/{}) doc={}: {}",
            request.notes.log_prefix, round_no + 1, max_rounds,
            request.document_id, ",".join(rejection_codes),
        )
        if round_no + 1 >= max_rounds:
            break
        gate_feedback = _build_retry_feedback(
            outcome, kept, dropped, request.scoring_chunks,
        )
        if stripped_labels:
            preview = "; ".join(stripped_labels[:5])
            gate_feedback = (
                f"{gate_feedback} 以下分组标签未见于原文, 已被剥除: {preview}。"
                f"group_path 各层必须逐字取自原文分组表头, 禁止编造或照抄示例。"
            ).strip()
        verdict_feedback = _verdict_gap_feedback(candidate_ledger)
        if verdict_feedback:
            gate_feedback = f"{gate_feedback} {verdict_feedback}".strip()
    meta["rounds"] = round_no + 1
    return _resolve_adoption(
        request, best,
        compat_expected=compat_expected, leaf_names=inventory_names,
    )


__all__ = [
    "ExtractCandidatesFn",
    "RescueChannelNotes",
    "RescueRoundsRequest",
    "run_candidate_rescue_rounds",
]
