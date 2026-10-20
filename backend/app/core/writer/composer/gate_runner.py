"""SectionComposer 后置评审子模块（Gate Pipeline + CoVe + Judge）

承载 7 个职责（Task 6 Step 1）：

- ``run_writer_post_llm_gate``  Writer Gate Pipeline post_llm 入口
- ``aggregate_gate_flags``      verdict 列表聚合为 draft.gate_flags JSON
- ``is_writer_critical_commitment``  关键承诺章节判定（CoVe/Judge 共用）
- ``maybe_run_writer_cove``     CoVe 复核
- ``merge_gate_flags``          Gate + CoVe 结果合并
- ``maybe_run_writer_judge``    Judge 抽样
- ``build_writer_judge_evidence``  judge/CoVe 共用证据组装

设计原则：
- 全部纯函数，第一参数接收 ``composer: "SectionComposer"`` 以复用其
  ``llm_client`` / ``db`` / ``_material_pool_size_cache`` 与共享 helper。
- 与原 ``SectionComposer`` 同名方法保持 byte-equal 行为：所有 fail-safe
  分支、import 顺序、字段截断长度、reason 拼接格式 100% 一致。
- 关键承诺关键词集合 ``WRITER_CRITICAL_TITLE_KEYWORDS`` /
  ``WRITER_COMMITMENT_KEYWORDS`` 在主类通过 ClassVar re-export，保留
  ``SectionComposer._WRITER_CRITICAL_TITLE_KEYWORDS`` 测试访问契约。
- ``maybe_run_writer_cove`` / ``maybe_run_writer_judge`` 内部仍走
  ``composer._is_writer_critical_commitment`` 与
  ``composer._build_writer_judge_evidence`` thin forwarder，让现有测试
  ``composer._build_writer_judge_evidence = AsyncMock(...)`` monkey patch
  契约继续生效。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from loguru import logger
from sqlalchemy import select

from app.core.ai.critic.flows import FLOW_WRITER
from app.core.writer.composer.judge_batch import JudgeJobSnapshot
from app.models.writing_clause import WritingClause, WritingClauseLayer
from app.models.writing_material import WritingMaterial

if TYPE_CHECKING:
    from app.core.writer.section_composer import ComposePayload, SectionComposer
    from app.models.writing_outline_node import WritingOutlineNode


# / B5.10 接入: 写标侧 Critic 高风险判定共用关键词集合。
# 与 maybe_run_writer_judge / maybe_run_writer_cove 共用，
# 避免 Judge / CoVe 对"关键承诺章节"的判断漂移。
WRITER_CRITICAL_TITLE_KEYWORDS: tuple[str, ...] = (
    "废标", "否决", "资质", "业绩", "承诺",
)
WRITER_COMMITMENT_KEYWORDS: tuple[str, ...] = (
    "废标", "否决", "资质", "业绩", "承诺",
    "保证", "声明", "服务期", "工期", "质保",
)


def run_writer_post_llm_gate(
    composer: "SectionComposer",
    project_id: int,
    node: "WritingOutlineNode",
    payload: "ComposePayload",
) -> list:
    """B5.6 Writer post_llm Gate Pipeline, fail-safe.

    Feature flag: WRITER_GATE_PIPELINE_RATIO (via should_use_gate_pipeline).

    P2.1b: 返回 verdict 列表, 由调用方聚合成 gate_flags 写入 draft。
    失败时返回空列表 (行为与原先相同 — fail-safe)。
    """
    try:
        from app.core.ai.gates import should_use_gate_pipeline, build_pipeline_for
        from app.core.ai.gates.base import GateContext
    except Exception:
        return []

    if not should_use_gate_pipeline("writer", project_id):
        return []

    try:
        _node_type = node.node_type.value if node.node_type else "unknown"
        _bound_materials = list(
            payload.bound_material_ids
            if payload.bound_material_ids is not None
            else payload.source_material_ids or []
        )
        _pool_size = composer._material_pool_size_cache.get(project_id, 0)
        _ctx = GateContext(
            flow=FLOW_WRITER,
            stage="post_llm",
            target=payload,
            evidence={
                "node_id": node.id,
                "node_title": node.title,
                "node_type": _node_type,
                "material_pool_size": _pool_size,
                "bound_materials": _bound_materials,
                "source_clause_ids": list(payload.source_clause_ids or []),
                "consumption_trace": payload.consumption_trace or {},
            },
            config={
                "project_id": project_id,
                "material_pool_size": _pool_size,
            },
        )
        _pipeline = build_pipeline_for("writer")
        return list(_pipeline.run(_ctx)) or []
    except Exception as e:
        logger.warning(f"Writer post_llm Gate Pipeline degraded: {e}")
        return []


def aggregate_gate_flags(verdicts: list) -> Optional[dict]:
    """P2.1b: 把 GateVerdict 列表压缩成 draft.gate_flags JSON 结构。

    仅收录 non-pass verdict (warn/reject/enforce_patch), 压缩 suggested_patch
    的关键字段 + verdict/severity/reason, 供前端章节列表橙标展示。
    """
    if not verdicts:
        return None
    flags: dict = {}
    for v in verdicts:
        name = getattr(v, "gate_name", None) or "UnknownGate"
        verdict_val = getattr(v, "verdict", "")
        if verdict_val in (None, "", "pass"):
            continue
        patch = getattr(v, "suggested_patch", None) or {}
        reason = getattr(v, "reason", "") or ""
        severity = getattr(v, "severity", "") or ""
        trace = getattr(v, "trace", None) or {}
        flags[name] = {
            "verdict": verdict_val,
            "severity": severity,
            "reason": reason[:200],
            "patch": {
                k: v for k, v in patch.items()
                if isinstance(k, str) and not k.startswith("_")
            },
            # trace 部分字段 (如 fill_rate / ai_phrase_count) 便于 UI 精确展示
            "trace": {
                k: trace.get(k) for k in (
                    "fill_rate", "density_per_1k", "ai_phrase_count",
                    "outcome", "total_td", "non_empty_td", "issue_count",
                )
                if k in trace
            },
        }
    return flags or None


def is_writer_critical_commitment(
    node: "WritingOutlineNode",
    payload: "ComposePayload",
) -> tuple[bool, str]:
    """判定写标章节是否落在"关键承诺"高风险路径。

 修复：CoVe / Judge 共用。

 Returns:
 (is_critical, commitment_keyword)
 - is_critical: 标题命中关键词 / 正文出现承诺类金额、日期、数量表述。
 - commitment_keyword: 命中的承诺关键词，作为 CoVe template_var；
 未命中时回退为 "关键承诺"。
 """
    _title = (node.title or "")
    _title_lower = _title.lower()
    for kw in WRITER_CRITICAL_TITLE_KEYWORDS:
        if kw in _title or kw.lower() in _title_lower:
            return True, kw

    _content = (payload.content or "") if payload else ""
    if not _content:
        return False, "关键承诺"

    for kw in WRITER_COMMITMENT_KEYWORDS:
        if kw in _content:
            # 正文命中承诺关键词，再叠加金额/日期/数量类承诺线索才升级为高风险
            if re.search(
                r"(?:[¥￥]\s*\d|\d+\s*(?:元|万元|亿元|%|％|天|日|月|年|台|套|项|人|名))",
                _content,
            ):
                return True, kw

    return False, "关键承诺"


async def maybe_run_writer_cove(
    composer: "SectionComposer",
    project_id: int,
    node: "WritingOutlineNode",
    payload: "ComposePayload",
) -> Optional[dict]:
    """B5.10 /: Writer CoVe 写标关键承诺章节复核, fail-safe.

 触发条件: COVE_ENABLED + 项目 hash 命中 + 关键承诺章节命中。
 证据: 章节正文（必填）+ 绑定素材 snippet + 硬约束文本。
 结果: 返回单个 gate_flags 条目（命名为 ``WriterCoVe``），由调用方
 在 ``_save_draft`` 之前 merge 进 ``gate_flags`` 一次落库；CoVe 不改
 正文，仅在 disagree / inconclusive 时挂 ``failure_state=needs_review``
 作为人工复核标记。
 """
    try:
        from app.core.ai.critic.cove import should_use_cove, run_cove
    except Exception:
        return None

    if not should_use_cove(project_id):
        return None

    is_critical, commitment_keyword = composer._is_writer_critical_commitment(
        node, payload,
    )
    if not is_critical:
        return None

    _content = (payload.content or "") if payload else ""
    if not _content.strip():
        return None

    try:
        from app.core.ai.ai_factory import get_judge_client
        _client = get_judge_client(
            project_id=int(project_id) if project_id else None,
        )
        if _client is None:
            return None

        # 证据组装：章节正文 + 绑定素材 + 硬约束
        evidence = await composer._build_writer_judge_evidence(
            node_id=node.id,
            source_material_ids=payload.source_material_ids or [],
            source_clause_ids=payload.source_clause_ids or [],
        )
        evidence_snippets: list[dict] = [
            {"page": None, "text": _content[:1500]},
        ]
        for mat in (evidence.get("bound_materials") or [])[:3]:
            _snippet = (mat.get("snippet") or "").strip()
            if _snippet:
                evidence_snippets.append({"page": None, "text": _snippet})
        for hc in (evidence.get("hard_constraints") or [])[:5]:
            _hc = str(hc or "").strip()
            if _hc:
                evidence_snippets.append({"page": None, "text": _hc})

        chunks = [_content]
        for mat in (evidence.get("bound_materials") or [])[:3]:
            _snippet = (mat.get("snippet") or "").strip()
            if _snippet:
                chunks.append(_snippet)

        _result = await run_cove(
            trigger="writer_commitment",
            template_vars={
                "commitment_keyword": commitment_keyword,
            },
            chunks=chunks,
            llm_client=_client,
            flow=FLOW_WRITER,
            evidence_snippets=evidence_snippets,
        )

        outcome = (_result.outcome or "").lower()
        # skipped 视为静默通过, 不写 gate_flags 避免污染 UI 橙标
        if outcome in ("", "skipped", "agree"):
            return None

        patch: dict[str, str] = {}
        if _result.should_escalate:
            patch["failure_state"] = "needs_review"

        reason_parts = [f"trigger=writer_commitment", f"outcome={outcome}"]
        if _result.reason:
            reason_parts.append(f"reason={_result.reason}")
        reason = "; ".join(reason_parts)[:200]

        return {
            "WriterCoVe": {
                "verdict": outcome,
                "severity": "advisory",
                "reason": reason,
                "patch": patch,
                "trace": {
                    "outcome": outcome,
                    "trigger": "writer_commitment",
                    "commitment_keyword": commitment_keyword,
                    "answers_count": len(_result.answers or []),
                    "reason_code": _result.reason or "",
                },
            },
        }
    except Exception as e:
        logger.debug(f"Writer CoVe degraded: {e}")
        return None


def merge_gate_flags(
    base: Optional[dict], extra: Optional[dict],
) -> Optional[dict]:
    """合并多个来源的 gate_flags（Gate Pipeline + CoVe），保持 None 语义。"""
    if not base and not extra:
        return None
    merged: dict = {}
    if base:
        merged.update(base)
    if extra:
        merged.update(extra)
    return merged or None


async def run_writer_judge_from_snapshot(
    composer: "SectionComposer",
    snapshot: JudgeJobSnapshot,
) -> None:
    """
    职责: 按不可变快照执行 Writer Judge 抽样。
    不做: 不改正文、不改 gate_flags、不重新判定高风险。
    """
    try:
        from app.core.ai.critic.judge_sampler import maybe_run_judge
    except Exception:
        return

    try:
        content = snapshot.content or ""
        content_preview = content[:600]
        content_for_judge = content[:3000]
        node_type_label = snapshot.node_type or "unknown"
        bound_materials_count = len(snapshot.source_material_ids)
        bound_clauses_count = len(snapshot.source_clause_ids)
        context_lines = [
            f"章节: [{node_type_label}] {snapshot.node_title or '(无标题)'}",
            f"节点ID: {snapshot.node_id}",
            f"正文字数: {len(content)}",
            f"绑定素材数: {bound_materials_count}",
            f"绑定条款数: {bound_clauses_count}",
        ]
        if content_preview:
            context_lines.append(f"正文摘要: {content_preview}")

        evidence = await composer._build_writer_judge_evidence(
            node_id=snapshot.node_id,
            source_material_ids=list(snapshot.source_material_ids),
            source_clause_ids=list(snapshot.source_clause_ids),
        )
        await maybe_run_judge(
            project_id=snapshot.project_id,
            original_output={
                "node_id": snapshot.node_id,
                "node_title": snapshot.node_title,
                "node_type": node_type_label,
                "word_count": len(content),
                "bound_materials_count": bound_materials_count,
                "bound_clauses_count": bound_clauses_count,
                "content": content_for_judge,
            },
            context_summary="\n".join(context_lines),
            flow="writer",
            is_high_risk=snapshot.is_high_risk,
            sample_key=f"{snapshot.project_id}:{snapshot.node_id}",
            evidence=evidence,
        )
    except Exception as e:
        logger.debug(f"Writer Judge degraded: {e}")


async def maybe_run_writer_judge(
    composer: "SectionComposer",
    project_id: int,
    node: "WritingOutlineNode",
    payload: "ComposePayload",
) -> None:
    """B5.12 Writer Judge sampling (10% + 100% high-risk), fail-safe.

 P1.2 升级:
 - original_output 增加 content (截断到 3000 字), 让 judge 看到正文;
 - evidence 入参携带 bound_materials (title + snippet) 与 hard_constraints
 文本, 支撑 judge 做事实/合规比对。

 修复：高风险判定改用 ``is_writer_critical_commitment`` 共用 helper,
 与 CoVe 触发条件保持一致。
 """
    try:
        from app.core.writer.composer.judge_batch import snapshot_from_compose

        has_critical, _ = composer._is_writer_critical_commitment(node, payload)
        snapshot = snapshot_from_compose(
            project_id=int(project_id),
            node=node,
            payload=payload,
            is_high_risk=has_critical,
        )
        await run_writer_judge_from_snapshot(composer, snapshot)
    except Exception as e:
        logger.debug(f"Writer Judge degraded: {e}")


async def build_writer_judge_evidence(
    composer: "SectionComposer",
    *,
    node_id: int,
    source_material_ids: list[int],
    source_clause_ids: list[int],
) -> dict:
    """P1.2: 为 writer judge 构造 evidence: 绑定素材 snippet + 硬约束文本。

    - bound_materials: 最多 3 份, 每份取 title + content 前 400 字;
    - hard_constraints: 取与本章节绑定的 hard_constraint 约束 content,
      最多 5 条, 每条 400 字。
    """
    evidence: dict[str, list] = {"bound_materials": [], "hard_constraints": []}

    try:
        if source_material_ids:
            # 并发协程共享 composer.db, judge/CoVe evidence 的 DB 读取
            # 必须与其它 DB 操作走同一把 _db_lock 串行化, 否则在云端高延迟下
            # 与持锁协程并发触发 isce (concurrent operations are not permitted)。
            async with composer._db_lock:
                result = await composer.db.execute(
                    select(WritingMaterial).where(
                        WritingMaterial.id.in_(source_material_ids[:3]),
                    )
                )
                mats = list(result.scalars().all())
            for m in mats:
                evidence["bound_materials"].append({
                    "title": (m.display_title or m.title or "")[:120],
                    "snippet": (m.content or "")[:400],
                })
    except Exception as exc:
        logger.debug(f"writer judge evidence materials degraded: {exc}")

    try:
        if source_clause_ids:
            # 同上, 硬约束读取同样纳入 _db_lock 保护。
            async with composer._db_lock:
                result = await composer.db.execute(
                    select(WritingClause).where(
                        WritingClause.id.in_(source_clause_ids),
                        WritingClause.layer == WritingClauseLayer.hard_constraint,
                    ).limit(5)
                )
                hard_clauses = list(result.scalars().all())
            for c in hard_clauses:
                evidence["hard_constraints"].append(
                    (c.content or "")[:400]
                )
    except Exception as exc:
        logger.debug(f"writer judge evidence hard_constraints degraded: {exc}")

    return evidence
