"""三元匹配引擎 -- 章节拉取式架构

四阶段流水线:
  Phase 1: 约束分类 (global / exclusive / shared)
  Phase 2: 章节拉取 (向量召回 + 结构预筛 + AI精排)
  Phase 3: 素材匹配 (委托 material_matching 语义命题管线: 类型门禁+命题召回+LLM句对裁决)
  Phase 4: 一致性校验 (孤儿/重复/排他 + 高置信自动确认)
"""

import asyncio
import json
import re
import time
from typing import Any, Callable, Optional

import numpy as np
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import get_review_client, LLMClient
from app.core.ai.retry_tracker import LLMRetryTracker
from app.core.config import settings
from app.core.observability.instrument import instrument as _observability_instrument
from app.core.writer.clause_scope_policy import (
    HIDDEN_COVERAGE_SUB_TYPES,
    WRITING_RELEVANCE_REFERENCE,
    build_relevance_mark,
    is_body_consumable_clause,
    is_manual_relevance_override,
    is_writing_guide_fallback_clause,
)
from app.core.writer.fixed_template_contract import FIXED_TEMPLATE_SNAPSHOT_KEY
from app.core.writer.numbering import strip_existing_number
from app.core.writer.prompts.clause_classify import get_clause_classify_prompt as _code_clause_classify
from app.core.writer.prompts.material_match import get_chapter_judge_prompt as _code_chapter_judge
from app.core.ai.prompt_registry import call_with_fallback, resolve_runtime_params
from app.core.writer.chapter_judge_runtime import (
    cap_judge_candidates,
    iter_judge_batches,
    merge_bind_true,
    try_unique_rule_bind,
)


# NOTE: call_with_fallback 通过 inspect.signature 自动把位置参数映射成
# 命名参数, 避免 Registry 命中命名占位符但调用方传位置参数时的静默 fallback
# (根治版); 原本要求调用方显式关键字, 此处改为工具保护。
def get_clause_classify_prompt(*a, **kw):
    return call_with_fallback("writing.clause_classify", _code_clause_classify, *a, **kw)

def get_chapter_judge_prompt(*a, **kw):
    return call_with_fallback("writing.chapter_judge", _code_chapter_judge, *a, **kw)
from app.core.arq_runtime import (
    ProjectCancelledError,
    is_project_cancelled,
    raise_if_project_cancelled,
)
from app.core.vectorizer.encode_gate import safe_encode
from app.core.writer.material_matching import (
    MaterialMatchingRequest,
    match_materials_to_chapters,
)
from app.core.writer.material_matching.binding_persistence import (
    BindingPersistenceDeps,
    persist_rematched_bindings,
)
from app.models.writing_binding import WritingBinding, BindingType, BindingStatus
from app.models.writing_clause import WritingClause
from app.models.writing_material import WritingMaterial
from app.models.writing_outline_node import WritingOutlineNode, OutlineNodeType

ProgressCallback = Callable[..., None]

_AUTO_CONFIRM_THRESHOLD = 0.85
_VECTOR_SIMILARITY_THRESHOLD = 0.65
_VECTOR_TOP_K = 10
_MAX_CLASSIFY_BATCH = 50
_MAX_CONCURRENT_CHAPTERS = 10
_MAX_SHARED_NODES = 3


def _clause_volume_type(clause: WritingClause) -> str:
    """读取 fixed_template clause 的 snapshot.volume_type, 缺失时返回空字符串。"""
    metadata = getattr(clause, "metadata_json", None)
    if not isinstance(metadata, dict):
        return ""
    snapshot = metadata.get(FIXED_TEMPLATE_SNAPSHOT_KEY)
    if not isinstance(snapshot, dict):
        return ""
    raw = snapshot.get("volume_type")
    return (str(raw).strip().lower() if raw else "")


def _clause_metadata_suffix(clause: WritingClause) -> str:
    metadata = getattr(clause, "metadata_json", None)
    if not isinstance(metadata, dict) or not metadata:
        return ""
    keys = [
        "target_section_hint", "evidence_hint", "material_kind",
        "required_form", "applies_to", "is_mandatory",
        "score_item_name", "max_score",
    ]
    compact = {
        key: metadata.get(key)
        for key in keys
        if metadata.get(key) not in (None, "", [], {})
    }
    if not compact:
        return ""
    return f", metadata={json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"


def _node_type_value(node: WritingOutlineNode) -> str:
    ntype = getattr(node, "node_type", "")
    return ntype.value if hasattr(ntype, "value") else str(ntype)


class MaterialMatcher:
    """章节拉取式三元匹配引擎"""

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        project_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self._classify_retry = LLMRetryTracker("matcher_classify")
        self._judge_retry = LLMRetryTracker("matcher_judge")

    # ==================== 主入口 ====================

    @_observability_instrument(
        name="writer_material_match_seconds",
        layer="stage",
        pillar="efficiency",
        direction="lower_better",
        business="writer",
        measure="latency",
        description="标书写作三元匹配单次耗时",
    )
    async def match(
        self,
        project_id: int,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[WritingBinding]:
        """四阶段匹配流水线"""
        clauses = await self._load_confirmed_clauses(project_id)
        nodes = await self._load_outline_nodes(project_id)
        materials = await self._load_materials(project_id)

        logger.info(
            f"章节拉取式匹配启动: project={project_id}, "
            f"clauses={len(clauses)}, nodes={len(nodes)}, materials={len(materials)}"
        )

        def _progress(stage: str, cur: int = 0, tot: int = 1, msg: str = "") -> None:
            if progress_callback:
                try:
                    progress_callback(stage, cur, tot, msg)
                except Exception:
                    pass

        # 阶段边界增加 cancellation 检查点，避免项目被并发删除后
        # 仍消耗 token 做无用 LLM/向量计算；命中墓碑直接抛 ProjectCancelledError
        # 由 writing_match_task 的 except 分支走静默清理。
        raise_if_project_cancelled(project_id)

        # Phase 1: 约束分类
        _progress("classifying", 0, 1, "约束作用域分类")
        t0 = time.time()
        global_clauses, target_clauses, scope_map = await self._classify_clauses(
            clauses, nodes,
            on_batch_done=lambda done, total: _progress(
                "classifying", done, total, f"约束分类 {done}/{total} 批",
            ),
        )
        logger.info(
            f"Phase 1 约束分类: global={len(global_clauses)}, "
            f"target={len(target_clauses)}, 耗时={time.time()-t0:.1f}s"
        )

        # Phase 2: 章节拉取
        raise_if_project_cancelled(project_id)
        _progress("retrieving", 0, 1, "章节匹配裁决")
        t1 = time.time()
        child_ids = {n.parent_id for n in nodes if n.parent_id is not None}
        leaf_chapters = [n for n in nodes if n.id not in child_ids]
        profile_stats = self._assess_chapter_profile_quality(leaf_chapters)
        self._report_chapter_profile_degradation(project_id, profile_stats)
        c2n = await self._retrieve_and_judge_all_chapters(
            leaf_chapters, target_clauses, scope_map, nodes,
            on_chapter_done=lambda done, total: _progress(
                "retrieving", done, total, f"章节裁决 {done}/{total}",
            ),
        )

        # Phase 2b: volume_shared 约束挂到分卷根节点（不展开到叶子）
        volume_shared_clauses = [
            c for c in target_clauses
            if scope_map.get(c.id, {}).get("scope") == "volume_shared"
        ]
        vs_count = 0

        volume_root_map: dict[str, int] = {}
        for n in nodes:
            vol = (n.volume_type or "").lower().strip()
            if vol and n.depth == 0:
                volume_root_map[vol] = n.id

        existing_c2n_keys = {
            (b["clause_id"], b["outline_node_id"])
            for b in c2n if b.get("clause_id") and b.get("outline_node_id")
        }

        # 收集每条约束已被 Phase 2 分配到的叶子节点所属分卷
        leaf_id_set = {ch.id for ch in leaf_chapters}
        node_vol_map = {n.id: (n.volume_type or "").lower().strip() for n in nodes}
        clause_assigned_volumes: dict[int, set[str]] = {}
        for b in c2n:
            cid = b.get("clause_id")
            nid = b.get("outline_node_id")
            if cid and nid and nid in leaf_id_set:
                vol = node_vol_map.get(nid, "")
                if vol:
                    clause_assigned_volumes.setdefault(cid, set()).add(vol)

        for c in volume_shared_clauses:
            hint = (scope_map[c.id].get("target_hint") or "").lower().strip()
            # 已通过 Phase 2 分配到该分卷下的子章节，跳过根节点挂载
            if hint and hint in clause_assigned_volumes.get(c.id, set()):
                continue
            root_id = volume_root_map.get(hint)
            if root_id and (c.id, root_id) not in existing_c2n_keys:
                c2n.append({
                    "binding_type": BindingType.clause_to_node,
                    "clause_id": c.id,
                    "outline_node_id": root_id,
                    "material_id": None,
                    "confidence": 0.8,
                    "source": "auto",
                    "reason": f"分卷级约束,适用于{hint}下所有自由论述章节",
                    "scope": "volume_shared",
                })
                existing_c2n_keys.add((c.id, root_id))
                vs_count += 1

        logger.info(
            f"Phase 2 章节拉取: bindings={len(c2n)}, "
            f"chapters={len(leaf_chapters)}, volume_shared={vs_count}, "
            f"耗时={time.time()-t1:.1f}s"
        )

        # Phase 3: 素材匹配
        raise_if_project_cancelled(project_id)
        _progress("materials", 2, 4, "素材关联")
        t2 = time.time()
        m2n = await self._match_materials(
            c2n, materials, clauses, nodes, project_id
        )
        logger.info(
            f"Phase 3 素材匹配: bindings={len(m2n)}, 耗时={time.time()-t2:.1f}s"
        )

        # Phase 4: 一致性校验
        raise_if_project_cancelled(project_id)
        _progress("validating", 3, 4, "一致性校验")
        all_bindings = c2n + m2n
        all_bindings = self._consistency_check(
            all_bindings, global_clauses, target_clauses, scope_map,
            leaf_chapters, all_nodes=nodes,
        )
        logger.info(f"Phase 4 一致性校验: final_bindings={len(all_bindings)}")

        # 保存
        _progress("saving", 0, 1, "保存绑定关系")
        saved = await self._save_bindings(project_id, all_bindings)
        _progress("completed", 1, 1, f"共 {len(saved)} 条绑定")

        logger.info(f"匹配完成: project={project_id}, total={len(saved)}")
        return saved

    @staticmethod
    def _assess_chapter_profile_quality(
        leaf_chapters: list[WritingOutlineNode],
    ) -> dict[str, int]:
        """统计章节画像缺失/弱画像数量，用于匹配质量降级提示。: 口径收敛到 chapter_profiling.quality SSOT。
 - missing: 无原始画像
 - weak/degraded: 有原始但非 matchable
 - ok: matchable 或 complete
 """
        from app.core.writer.chapter_profiling.quality import (
            ProfileQuality,
            assess_profile_raw,
        )

        stats = {
            "total": len(leaf_chapters),
            "missing": 0,
            "weak": 0,
            "ok": 0,
            "complete": 0,
            "matchable": 0,
            "degraded": 0,
        }
        for chapter in leaf_chapters:
            raw = getattr(chapter, "chapter_profile", None)
            if not raw:
                stats["missing"] += 1
                stats["degraded"] += 1
                continue
            quality = assess_profile_raw(raw)
            if quality == ProfileQuality.COMPLETE:
                stats["ok"] += 1
                stats["complete"] += 1
            elif quality == ProfileQuality.MATCHABLE:
                stats["ok"] += 1
                stats["matchable"] += 1
            else:
                stats["weak"] += 1
                stats["degraded"] += 1
        return stats

    @staticmethod
    def _report_chapter_profile_degradation(
        project_id: int,
        stats: dict[str, int],
    ) -> None:
        """记录章节画像降级信号，不阻断匹配主流程。"""
        total = stats.get("total", 0)
        degraded = stats.get("missing", 0) + stats.get("weak", 0)
        if total <= 0 or degraded <= 0:
            return
        logger.warning(
            "章节画像降级: project={}, total={}, missing={}, weak={}, ok={}",
            project_id,
            total,
            stats.get("missing", 0),
            stats.get("weak", 0),
            stats.get("ok", 0),
        )
        try:
            from app.core.observability.degradation_aggregator import (
                record_degradation as _record_degradation,
            )
            if stats.get("missing", 0):
                _record_degradation(
                    kind="chapter_profile_missing",
                    ref=str(project_id),
                    reason=f"{stats['missing']}/{total} leaf chapters missing profile",
                )
            if stats.get("weak", 0):
                _record_degradation(
                    kind="chapter_profile_weak",
                    ref=str(project_id),
                    reason=f"{stats['weak']}/{total} leaf chapters have weak profile",
                )
        except Exception:
            pass

    # ==================== Phase 1: 约束分类 ====================

    async def _classify_clauses(
        self,
        clauses: list[WritingClause],
        nodes: list[WritingOutlineNode] | None = None,
        on_batch_done: Callable[[int, int], None] | None = None,
    ) -> tuple[list[WritingClause], list[WritingClause], dict[int, dict]]:
        """AI 约束作用域分类 + 分类结果校验

        Returns:
            (global_clauses, target_clauses, scope_map)
            scope_map: {clause_id: {"scope": str, "target_hint": str|None, "reason": str}}
        """
        if not clauses:
            return [], [], {}

        scope_map: dict[int, dict] = {}
        outline_candidates_text = self._format_outline_candidates(nodes or [])

        batches = [
            clauses[i:i + _MAX_CLASSIFY_BATCH]
            for i in range(0, len(clauses), _MAX_CLASSIFY_BATCH)
        ]

        async def _classify_one_batch(
            batch: list[WritingClause],
        ) -> dict[int, dict]:
            """单批分类，返回 {clause_id: scope_info}"""
            batch_map: dict[int, dict] = {}
            clauses_text = "\n".join(
                f"- ID={c.id}, 类型={c.sub_type}, 层级={c.layer.value if hasattr(c.layer, 'value') else c.layer}, "
                f"内容={c.content[:150]}{_clause_metadata_suffix(c)}"
                for c in batch
            )
            # 必须使用关键字参数: Registry user_template 的 {clauses_text} 是命名占位符,
            # 位置参数会让 str.format 抛 KeyError 并 fallback 到代码默认, 导致 LLM Center
            # 对 writing.clause_classify 节点的在线编辑永远不生效。
            system_msg, user_msg = get_clause_classify_prompt(
                clauses_text=clauses_text,
                outline_candidates_text=outline_candidates_text,
            )
            temperature, max_tokens = resolve_runtime_params(
                "writing.clause_classify", 0.1, 4000,
            )

            try:
                result = await self.llm_client.chat_json(
                    user_message=user_msg,
                    system_message=system_msg,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                logger.warning(f"约束分类AI调用失败: {e}, 降级全部为shared")
                b = batch
                self._classify_retry.record(
                    key=f"batch_{len(b)}clauses",
                    retry_coro_factory=lambda _b=b: _classify_one_batch(_b),
                    fallback_applied="all_shared",
                )
                try:
                    from app.core.observability.degradation_aggregator import (
                        record_degradation as _record_degradation,
                    )
                    _record_degradation(
                        kind="classify_failed",
                        ref=f"batch_{len(batch)}clauses",
                        reason=f"{type(e).__name__}: {str(e)[:120]}",
                    )
                except Exception:
                    pass
                for c in batch:
                    batch_map[c.id] = {
                        "scope": "shared", "target_hint": None,
                        "reason": "分类失败,降级为shared",
                    }
                return batch_map

            if not isinstance(result, list):
                for c in batch:
                    batch_map[c.id] = {
                        "scope": "shared", "target_hint": None,
                        "reason": "返回非数组,降级为shared",
                    }
                return batch_map

            classified_ids: set[int] = set()
            for item in result:
                if not isinstance(item, dict):
                    continue
                cid = item.get("clause_id")
                scope_val = item.get("scope", "shared")
                if scope_val not in ("global", "exclusive", "volume_shared", "shared", "irrelevant"):
                    scope_val = "shared"
                if cid is not None:
                    batch_map[cid] = {
                        "scope": scope_val,
                        "target_hint": item.get("target_hint"),
                        "reason": item.get("reason", ""),
                    }
                    classified_ids.add(cid)

            for c in batch:
                if c.id not in classified_ids:
                    batch_map[c.id] = {
                        "scope": "shared", "target_hint": None,
                        "reason": "AI未返回,降级为shared",
                    }
            return batch_map

        _batch_done_count = 0
        _batch_total = len(batches)

        async def _classify_and_report(batch: list[WritingClause]) -> dict[int, dict]:
            nonlocal _batch_done_count
            try:
                return await _classify_one_batch(batch)
            finally:
                _batch_done_count += 1
                if on_batch_done:
                    on_batch_done(_batch_done_count, _batch_total)

        batch_results = await asyncio.gather(
            *[_classify_and_report(b) for b in batches],
            return_exceptions=True,
        )

        for i, result in enumerate(batch_results):
            if isinstance(result, Exception):
                logger.warning(f"约束分类并行任务异常: batch={i}, err={result}")
                for c in batches[i]:
                    scope_map[c.id] = {
                        "scope": "shared", "target_hint": None,
                        "reason": "并行异常,降级为shared",
                    }
            else:
                scope_map.update(result)

        if self._classify_retry.has_failures:
            stats = await self._classify_retry.retry_all_concurrent(max_concurrency=3)
            for detail in stats.details:
                if detail["success"] and isinstance(detail["result"], dict):
                    for cid, info in detail["result"].items():
                        if not info.get("reason", "").startswith("分类失败"):
                            scope_map[cid] = info

        # 分类结果校验
        node_titles_lower = []
        if nodes:
            node_titles_lower = [n.title.lower().strip() for n in nodes]

        corrections = 0
        for c in clauses:
            info = scope_map.get(c.id, {})
            scope = info.get("scope", "shared")

            # (a) fixed_template 不能是 global -> 强制降级为 exclusive
            if scope == "global" and c.sub_type == "fixed_template":
                scope_map[c.id]["scope"] = "exclusive"
                scope_map[c.id]["reason"] = f"[校验修正] fixed_template不能是全局: {info.get('reason', '')}"
                corrections += 1

            # (b) exclusive 必须有可匹配目标 -> 找不到则降级为 shared
            if scope == "exclusive" and node_titles_lower:
                hint = (info.get("target_hint") or "").lower().strip()
                if not hint or not any(
                    hint in t or t in hint for t in node_titles_lower
                ):
                    scope_map[c.id]["scope"] = "shared"
                    scope_map[c.id]["reason"] = f"[校验修正] exclusive无匹配目标: {info.get('reason', '')}"
                    corrections += 1

        if corrections > 0:
            logger.info(f"分类校验修正: {corrections}条约束作用域被调整")

        # irrelevant 约束不再静默丢弃。AI 判定与正文编写无关时,
        # 落 writing_relevance=reference 供覆盖统计/前端折叠区统一消费;
        # 人工恢复(source=manual)的约束仍按 shared 进入匹配。
        if settings.WRITING_IRRELEVANT_FOLD_ENABLED:
            for c in clauses:
                info = scope_map.get(c.id, {})
                if info.get("scope") != "irrelevant":
                    continue
                if is_manual_relevance_override(c):
                    info["scope"] = "shared"
                    info["reason"] = "[人工恢复正文约束] " + str(info.get("reason", ""))
                    continue
                c.metadata_json = build_relevance_mark(
                    getattr(c, "metadata_json", None),
                    level=WRITING_RELEVANCE_REFERENCE,
                    source="matcher",
                    reason=str(info.get("reason") or "AI判定不影响标书正文编写"),
                )

        irrelevant_count = sum(
            1 for c in clauses
            if scope_map.get(c.id, {}).get("scope") == "irrelevant"
        )
        if irrelevant_count > 0:
            logger.info(f"irrelevant约束折叠为参考: {irrelevant_count}条与编写无关")

        global_clauses = [
            c for c in clauses
            if scope_map.get(c.id, {}).get("scope") == "global"
        ]
        target_clauses = [
            c for c in clauses
            if scope_map.get(c.id, {}).get("scope") not in ("global", "irrelevant")
        ]

        return global_clauses, target_clauses, scope_map

    @staticmethod
    def _format_outline_candidates(nodes: list[WritingOutlineNode]) -> str:
        """格式化真实目录候选，约束分类的 target_hint 只能从这里选择。"""
        if not nodes:
            return "（未提供候选章节，无法确定时请默认 shared）"

        child_ids = {n.parent_id for n in nodes if n.parent_id is not None}
        lines: list[str] = []
        seen_volumes: set[tuple[str, str]] = set()
        for node in nodes:
            volume_type = (getattr(node, "volume_type", None) or "").strip()
            title = (getattr(node, "title", "") or "").strip()
            if not title:
                continue
            if getattr(node, "depth", 0) == 0 or (volume_type and node.parent_id is None):
                key = (volume_type or "default", title)
                if key not in seen_volumes:
                    lines.append(
                        f"- 候选分卷: {title} | volume_type={volume_type or 'default'}"
                    )
                    seen_volumes.add(key)
            if node.id not in child_ids:
                lines.append(
                    f"- 候选章节: {title} | node_type={_node_type_value(node)} "
                    f"| volume_type={volume_type or 'default'}"
                )

        return "\n".join(lines) if lines else "（未提供候选章节，无法确定时请默认 shared）"

    # ==================== Phase 2: 章节拉取 ====================

    async def _retrieve_and_judge_all_chapters(
        self,
        leaf_chapters: list[WritingOutlineNode],
        target_clauses: list[WritingClause],
        scope_map: dict[int, dict],
        all_nodes: list[WritingOutlineNode],
        on_chapter_done: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """并发遍历叶子章节，每个章节独立执行候选召回+AI裁决"""
        if not leaf_chapters or not target_clauses:
            return []

        node_map = {n.id: n for n in all_nodes}
        sem = asyncio.Semaphore(_MAX_CONCURRENT_CHAPTERS)
        _ch_done_count = 0
        _ch_total = len(leaf_chapters)

        async def _process_one(chapter: WritingOutlineNode) -> list[dict]:
            nonlocal _ch_done_count
            try:
                async with sem:
                    candidates = self._retrieve_candidates(
                        chapter, target_clauses, scope_map, node_map
                    )
                    if not candidates:
                        return []
                    exclusive_unique = False
                    volume_gated_unique = False
                    if len(candidates) == 1:
                        info = scope_map.get(candidates[0].id, {})
                        hint = (info.get("target_hint") or "").lower().strip()
                        exclusive_unique = (
                            info.get("scope") == "exclusive"
                            and bool(hint)
                            and self._fuzzy_title_match(
                                hint, chapter.title.lower().strip(),
                            )
                        )
                        ntype = chapter.node_type
                        ntype_val = (
                            ntype.value if hasattr(ntype, "value") else str(ntype)
                        )
                        chapter_volume = (
                            getattr(chapter, "volume_type", None) or ""
                        ).strip().lower()
                        volume_gated_unique = (
                            ntype_val == "fixed_template"
                            and bool(chapter_volume)
                            and candidates[0].sub_type == "fixed_template"
                            and _clause_volume_type(candidates[0])
                            in (chapter_volume, "")
                        )
                    shortcut = try_unique_rule_bind(
                        candidates,
                        exclusive_unique=exclusive_unique,
                        volume_gated_unique=volume_gated_unique,
                    )
                    if shortcut is not None:
                        reason = (
                            "rule_unique_exclusive"
                            if exclusive_unique
                            else "rule_unique_volume_template"
                        )
                        return [{
                            "binding_type": BindingType.clause_to_node,
                            "clause_id": shortcut.id,
                            "outline_node_id": chapter.id,
                            "material_id": None,
                            "confidence": 0.9,
                            "source": "auto",
                            "reason": reason,
                        }]
                    return await self._ai_judge_chapter(
                        chapter, candidates, node_map
                    )
            finally:
                _ch_done_count += 1
                if on_chapter_done:
                    on_chapter_done(_ch_done_count, _ch_total)

        results = await asyncio.gather(
            *[_process_one(ch) for ch in leaf_chapters],
            return_exceptions=True,
        )

        bindings: list[dict] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    f"章节裁决异常: chapter={leaf_chapters[i].title}, err={result}"
                )
                continue
            bindings.extend(result)

        if self._judge_retry.has_failures:
            stats = await self._judge_retry.retry_all_concurrent(max_concurrency=5)
            for detail in stats.details:
                if detail["success"] and detail["result"]:
                    bindings.extend(detail["result"])

        return bindings

    def _retrieve_candidates(
        self,
        chapter: WritingOutlineNode,
        target_clauses: list[WritingClause],
        scope_map: dict[int, dict],
        node_map: dict[int, WritingOutlineNode],
    ) -> list[WritingClause]:
        """双路候选召回: 语义路径(向量) + 结构路径(规则)

        短标题章节(<5中文字)自动降低向量阈值和关键词门槛。
        """
        candidate_ids: set[int] = set()
        candidates: list[WritingClause] = []

        chapter_title_lower = chapter.title.lower().strip()
        title_cn_len = len(re.findall(r"[\u4e00-\u9fff]", chapter.title))

        # 结构路径: exclusive 的 target_hint 模糊匹配
        for c in target_clauses:
            info = scope_map.get(c.id, {})
            if info.get("scope") == "exclusive":
                hint = (info.get("target_hint") or "").lower().strip()
                if hint and self._fuzzy_title_match(hint, chapter_title_lower):
                    if c.id not in candidate_ids:
                        candidates.append(c)
                        candidate_ids.add(c.id)

        # 结构路径: node_type 亲和性
        ntype = chapter.node_type
        ntype_val = ntype.value if hasattr(ntype, 'value') else str(ntype)
        for c in target_clauses:
            if c.id in candidate_ids:
                continue
            if ntype_val == "fixed_template" and c.sub_type == "fixed_template":
                candidates.append(c)
                candidate_ids.add(c.id)
            elif ntype_val == "data_fill" and c.sub_type in (
                "material_required", "content_required", "table_format"
            ):
                candidates.append(c)
                candidate_ids.add(c.id)

        # 语义路径: 向量相似度（短标题自适应降低阈值）
        effective_threshold = 0.55 if title_cn_len < 5 else _VECTOR_SIMILARITY_THRESHOLD
        if chapter.embedding is not None:
            chapter_emb = np.array(chapter.embedding)
            scored: list[tuple[WritingClause, float]] = []
            for c in target_clauses:
                if c.id in candidate_ids:
                    continue
                if c.embedding is not None:
                    c_emb = np.array(c.embedding)
                    sim = self._cosine_similarity(chapter_emb, c_emb)
                    if sim >= effective_threshold:
                        scored.append((c, sim))

            scored.sort(key=lambda x: x[1], reverse=True)
            for c, _ in scored[:_VECTOR_TOP_K]:
                if c.id not in candidate_ids:
                    candidates.append(c)
                    candidate_ids.add(c.id)

        # 画像 expects 关键词补充（短标题自适应降低门槛）
        min_kw_overlap = 2 if title_cn_len < 5 else 3
        profile = self._parse_profile(chapter)
        if profile and profile.get("expects"):
            expects_text = " ".join(profile["expects"])
            expects_kws = set(re.findall(r"[\u4e00-\u9fff]{2,}", expects_text))
            for c in target_clauses:
                if c.id in candidate_ids:
                    continue
                clause_kws = set(re.findall(r"[\u4e00-\u9fff]{2,}", c.content))
                if len(expects_kws & clause_kws) >= min_kw_overlap:
                    candidates.append(c)
                    candidate_ids.add(c.id)
                    if len(candidates) >= _VECTOR_TOP_K + 10:
                        break

        # fixed_template 节点候选必须按 volume_type 严格守门,
        # 防止 chapter_judge 把 technical 模板错绑给 commercial 节点 (反之亦然)。
        # 节点或 clause 任一 volume_type 为空时维持原行为, 兼容旧项目数据。
        chapter_volume = (getattr(chapter, "volume_type", None) or "").strip().lower()
        if ntype_val == "fixed_template" and chapter_volume:
            candidates = [
                c for c in candidates
                if c.sub_type != "fixed_template"
                or _clause_volume_type(c) in (chapter_volume, "")
            ]

        return cap_judge_candidates(
            candidates,
            int(getattr(settings, "WRITING_CHAPTER_JUDGE_MAX_CANDIDATES", 40)),
        )

    async def _ai_judge_chapter(
        self,
        chapter: WritingOutlineNode,
        candidates: list[WritingClause],
        node_map: dict[int, WritingOutlineNode],
    ) -> list[dict]:
        """AI 精排裁决: 基于章节画像判断候选约束是否真正属于本章节"""
        profile = self._parse_profile(chapter)
        semantic_role = profile.get("semantic_role", chapter.title) if profile else chapter.title
        expects = ", ".join(profile.get("expects", [])) if profile else ""
        rejects = ", ".join(profile.get("rejects", [])) if profile else ""

        # 构建祖先路径
        parts = []
        cur = chapter
        while cur:
            parts.append(cur.title)
            cur = node_map.get(cur.parent_id) if cur.parent_id else None
        chapter_path = " > ".join(reversed(parts))

        ntype = chapter.node_type
        chapter_type = ntype.value if hasattr(ntype, 'value') else str(ntype)
        temperature, max_tokens = resolve_runtime_params(
            "writing.chapter_judge", 0.1, 4000,
        )
        batch_size = int(getattr(settings, "WRITING_CHAPTER_JUDGE_BATCH_SIZE", 20))
        capped = cap_judge_candidates(
            candidates,
            int(getattr(settings, "WRITING_CHAPTER_JUDGE_MAX_CANDIDATES", 40)),
        )
        batch_results: list[Any] = []

        try:
            for batch in iter_judge_batches(capped, batch_size):
                candidates_text = "\n".join(
                    f"- ID={c.id}, 类型={c.sub_type}, "
                    f"内容={c.content[:200]}{_clause_metadata_suffix(c)}"
                    for c in batch
                )
                system_msg, user_msg = get_chapter_judge_prompt(
                    chapter_title=chapter.title,
                    chapter_type=chapter_type,
                    chapter_path=chapter_path,
                    semantic_role=semantic_role,
                    expects=expects,
                    rejects=rejects,
                    candidates_text=candidates_text,
                )
                result = await self.llm_client.chat_json(
                    user_message=user_msg,
                    system_message=system_msg,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                batch_results.append(result)
        except Exception as e:
            logger.warning(f"章节裁决AI调用失败: chapter={chapter.title}, err={e}")
            ch, cands, nm = chapter, candidates, node_map
            self._judge_retry.record(
                key=f"chapter_{chapter.title}",
                retry_coro_factory=lambda _c=ch, _ca=cands, _n=nm: self._ai_judge_chapter(_c, _ca, _n),
                fallback_applied="empty_bindings",
            )
            try:
                from app.core.observability.degradation_aggregator import (
                    record_degradation as _record_degradation,
                )
                _record_degradation(
                    kind="chapter_judge_failed",
                    ref=str(getattr(chapter, "id", chapter.title)),
                    reason=f"{type(e).__name__}: {str(e)[:120]}",
                )
            except Exception:
                pass
            return []

        result = merge_bind_true(batch_results)
        clause_id_set = {c.id for c in capped}
        bindings: list[dict] = []

        for item in result:
            if not isinstance(item, dict):
                continue
            cid = item.get("clause_id")
            should_bind = item.get("should_bind", False)
            if not should_bind or cid not in clause_id_set:
                continue

            conf = min(1.0, max(0.0, float(item.get("confidence", 0.5))))
            reason = str(item.get("reason", ""))[:300]

            bindings.append({
                "binding_type": BindingType.clause_to_node,
                "clause_id": cid,
                "outline_node_id": chapter.id,
                "material_id": None,
                "confidence": conf,
                "source": "auto",
                "reason": reason,
            })

        return bindings

    # ==================== Phase 3: 素材匹配 ====================

    async def _match_materials(
        self,
        c2n_bindings: list[dict],
        materials: list[WritingMaterial],
        clauses: list[WritingClause],
        nodes: list[WritingOutlineNode],
        project_id: int,
    ) -> list[dict]:
        """素材->章节匹配: 委托 material_matching 管线(类型硬门禁 + 完整命题语义召回 + LLM 句对蕴含裁决)。: 旧的"多路召回 + Top-N 直接绑定"改为语义命题匹配管线，通用证明的高相似
 近邻不再直接成边；精度由类型硬门禁、完整命题召回与句对裁决 + 决策策略共同保证。
 本方法为最薄接入点: 组装请求 -> 调用管线 -> 把绑定提案转为 raw binding dict。
 c2n_bindings 作为约束->章节中间边随请求下传，保留供管线后续增强使用。
 """
        request = MaterialMatchingRequest(
            project_id=project_id,
            materials=materials,
            nodes=nodes,
            clauses=clauses,
            c2n_bindings=c2n_bindings,
        )
        proposals = await match_materials_to_chapters(
            request, llm_client=self.llm_client, encoder=safe_encode
        )
        bindings = [
            {
                "binding_type": BindingType.material_to_node,
                "clause_id": None,
                "outline_node_id": proposal.outline_node_id,
                "material_id": proposal.material_id,
                "confidence": proposal.confidence,
                "source": proposal.source,
                "reason": (
                    f"待复核: {proposal.reason}"
                    if proposal.needs_review and proposal.scope != "orphan"
                    else proposal.reason
                ),
                "scope": proposal.scope,
            }
            for proposal in proposals
        ]
        logger.info(
            "素材匹配(管线): project={}, materials={}, bindings={}",
            project_id,
            len(materials),
            len(bindings),
        )
        return bindings

    # ==================== Phase 4: 一致性校验 ====================

    def _consistency_check(
        self,
        all_bindings: list[dict],
        global_clauses: list[WritingClause],
        target_clauses: list[WritingClause],
        scope_map: dict[int, dict],
        leaf_chapters: list[WritingOutlineNode] | None = None,
        all_nodes: list[WritingOutlineNode] | None = None,
    ) -> list[dict]:
        """一致性校验: 全局约束写入 + 孤儿检测 + exclusive排他 + shared预算 + 零绑定回填"""

        # (a) 全局约束写入 DB（内容生成阶段需消费）
        for c in global_clauses:
            all_bindings.append({
                "binding_type": BindingType.clause_to_node,
                "clause_id": c.id,
                "outline_node_id": None,
                "material_id": None,
                "confidence": 1.0,
                "source": "auto",
                "reason": "全局约束,适用于所有章节",
                "scope": "global",
            })

        # (b) 孤儿约束: 定向约束中未被任何章节选中
        bound_clause_ids = {
            b["clause_id"] for b in all_bindings
            if b.get("binding_type") == BindingType.clause_to_node and b.get("clause_id")
        }
        for c in target_clauses:
            if c.id not in bound_clause_ids:
                info = scope_map.get(c.id, {})
                if info.get("scope") != "global":
                    if (
                        settings.WRITING_GUIDE_FALLBACK_ENABLED
                        and is_writing_guide_fallback_clause(c)
                    ):
                        all_bindings.append({
                            "binding_type": BindingType.clause_to_node,
                            "clause_id": c.id,
                            "outline_node_id": None,
                            "material_id": None,
                            "confidence": 0.6,
                            "source": "auto",
                            "reason": "写作引导未匹配到具体章节，按全局引导注入",
                            "scope": "global",
                        })
                        continue
                    all_bindings.append({
                        "binding_type": BindingType.clause_to_node,
                        "clause_id": c.id,
                        "outline_node_id": None,
                        "material_id": None,
                        "confidence": 0.0,
                        "source": "auto",
                        "reason": "孤儿约束: 未被任何章节选中,待人工分配",
                        "scope": "orphan",
                    })

        # (b2) 孤儿约束语义去重: embedding 相似度 > 0.85 的只保留第一条
        orphan_bindings = [b for b in all_bindings if b.get("scope") == "orphan"]
        if len(orphan_bindings) > 1:
            clause_map_local = {c.id: c for c in target_clauses}
            orphan_remove_ids: set[int] = set()
            kept_ids: list[int] = []

            for ob in orphan_bindings:
                cid = ob.get("clause_id")
                if not cid or id(ob) in orphan_remove_ids:
                    continue
                c_obj = clause_map_local.get(cid)
                if not c_obj or c_obj.embedding is None:
                    kept_ids.append(cid)
                    continue

                is_dup = False
                c_emb = np.array(c_obj.embedding)
                for kept_cid in kept_ids:
                    kept_obj = clause_map_local.get(kept_cid)
                    if not kept_obj or kept_obj.embedding is None:
                        continue
                    sim = self._cosine_similarity(c_emb, np.array(kept_obj.embedding))
                    if sim > 0.85:
                        is_dup = True
                        break

                if is_dup:
                    orphan_remove_ids.add(id(ob))
                else:
                    kept_ids.append(cid)

            if orphan_remove_ids:
                all_bindings = [b for b in all_bindings if id(b) not in orphan_remove_ids]
                logger.info(f"孤儿约束去重: 移除{len(orphan_remove_ids)}条重复")

        # (c) exclusive 排他: 只保留最高置信的1条，其余强制移除
        exclusive_bindings: dict[int, list[dict]] = {}
        for b in all_bindings:
            if b.get("binding_type") != BindingType.clause_to_node:
                continue
            cid = b.get("clause_id")
            if not cid:
                continue
            info = scope_map.get(cid, {})
            if info.get("scope") == "exclusive" and b.get("outline_node_id"):
                exclusive_bindings.setdefault(cid, []).append(b)

        remove_ids: set[int] = set()
        for cid, bs in exclusive_bindings.items():
            if len(bs) <= 1:
                continue
            bs.sort(key=lambda x: x.get("confidence", 0), reverse=True)
            for b in bs[1:]:
                remove_ids.add(id(b))

        if remove_ids:
            all_bindings = [b for b in all_bindings if id(b) not in remove_ids]

        # (d) shared 约束预算: 每条 shared 约束最多绑定 _MAX_SHARED_NODES 个章节
        clause_node_bindings: dict[int, list[dict]] = {}
        for b in all_bindings:
            if (b.get("binding_type") == BindingType.clause_to_node
                    and b.get("outline_node_id") and b.get("clause_id")):
                cid = b["clause_id"]
                info = scope_map.get(cid, {})
                if info.get("scope") == "shared":
                    clause_node_bindings.setdefault(cid, []).append(b)

        shared_remove_ids: set[int] = set()
        for cid, bs in clause_node_bindings.items():
            if len(bs) <= _MAX_SHARED_NODES:
                continue
            bs.sort(key=lambda x: x.get("confidence", 0), reverse=True)
            for b in bs[_MAX_SHARED_NODES:]:
                shared_remove_ids.add(id(b))

        if shared_remove_ids:
            all_bindings = [b for b in all_bindings if id(b) not in shared_remove_ids]
            logger.info(f"shared预算裁剪: 移除{len(shared_remove_ids)}条超额绑定")

        # (e) 零绑定回填: required 叶子章节不允许完全为空（检查祖先链覆盖）
        if leaf_chapters:
            node_binding_count: dict[int, int] = {}
            for b in all_bindings:
                nid = b.get("outline_node_id")
                if nid and b.get("binding_type") == BindingType.clause_to_node:
                    node_binding_count[nid] = node_binding_count.get(nid, 0) + 1

            node_map_local = {n.id: n for n in (all_nodes or leaf_chapters or [])}

            backfill_candidates = [
                c for c in global_clauses
                if c.sub_type in (
                    "scoring_criterion",
                    "content_required",
                    "disqualification",
                    "emphasis_point",
                )
            ][:3]

            backfill_count = 0
            for ch in leaf_chapters:
                if ch.is_required and node_binding_count.get(ch.id, 0) == 0:
                    # 检查祖先链是否有覆盖（volume_shared 或其他约束）
                    has_ancestor_coverage = False
                    ancestor_id = ch.parent_id
                    while ancestor_id:
                        if node_binding_count.get(ancestor_id, 0) > 0:
                            has_ancestor_coverage = True
                            break
                        ancestor_node = node_map_local.get(ancestor_id)
                        ancestor_id = ancestor_node.parent_id if ancestor_node else None
                    if has_ancestor_coverage:
                        continue

                    for c in backfill_candidates:
                        all_bindings.append({
                            "binding_type": BindingType.clause_to_node,
                            "clause_id": c.id,
                            "outline_node_id": ch.id,
                            "material_id": None,
                            "confidence": 0.5,
                            "source": "auto",
                            "reason": "[零绑定回填] 从全局约束中补充",
                            "scope": "backfill",
                        })
                        backfill_count += 1

            if backfill_count > 0:
                logger.info(f"零绑定回填: 补充{backfill_count}条绑定")

        # (f) "其他资料"类章节跨章节去重：已在具体章节分配的约束从兜底章节及其子孙移除
        #
        # 识别策略（整句匹配，避免字词截断导致的语义断裂）：
        #   1. 标题整句包含"其他资料""其它资料""其他材料""其它材料""附录""补充材料"等短语
        #   2. 标题包含"其他证明""其它证明""必要的其他""必要的其它"等惯用表述
        #   3. 同级中排序最后（sort_order 最大）且标题含"其他"/"其它"
        _CATCHALL_PHRASES = [
            "其他资料", "其它资料", "其他材料", "其它材料",
            "附录", "补充材料", "补充资料",
            "其他证明", "其它证明", "必要的其他", "必要的其它",
        ]

        def _is_catchall_title(title: str) -> bool:
            title_lower = title.lower().strip()
            return any(phrase in title_lower for phrase in _CATCHALL_PHRASES)

        catchall_root_ids: set[int] = set()
        if all_nodes:
            for n in all_nodes:
                if _is_catchall_title(n.title):
                    catchall_root_ids.add(n.id)

        # 扩展到兜底节点的所有子孙
        catchall_tree_ids: set[int] = set()
        if catchall_root_ids and all_nodes:
            node_children_map: dict[int, list[int]] = {}
            for n in all_nodes:
                if n.parent_id is not None:
                    node_children_map.setdefault(n.parent_id, []).append(n.id)

            queue = list(catchall_root_ids)
            while queue:
                nid = queue.pop()
                catchall_tree_ids.add(nid)
                queue.extend(node_children_map.get(nid, []))

        non_catchall_node_ids = set()
        if all_nodes:
            all_node_ids = {n.id for n in all_nodes}
            non_catchall_node_ids = all_node_ids - catchall_tree_ids

        if catchall_tree_ids:
            clause_in_specific: set[int] = set()
            for b in all_bindings:
                if (b.get("binding_type") == BindingType.clause_to_node
                        and b.get("clause_id") and b.get("outline_node_id")
                        and b["outline_node_id"] in non_catchall_node_ids):
                    clause_in_specific.add(b["clause_id"])

            catchall_dedup_ids: set[int] = set()
            for b in all_bindings:
                if (b.get("binding_type") == BindingType.clause_to_node
                        and b.get("clause_id") and b.get("outline_node_id")
                        and b["outline_node_id"] in catchall_tree_ids
                        and b["clause_id"] in clause_in_specific):
                    catchall_dedup_ids.add(id(b))

            if catchall_dedup_ids:
                all_bindings = [b for b in all_bindings if id(b) not in catchall_dedup_ids]
                logger.info(
                    f"其他资料去重: 移除{len(catchall_dedup_ids)}条"
                    f"(兜底节点{len(catchall_root_ids)}个,含子孙共{len(catchall_tree_ids)}个)"
                )

        # scope 字段回填
        for b in all_bindings:
            cid = b.get("clause_id")
            if cid and cid in scope_map and "scope" not in b:
                b["scope"] = scope_map[cid]["scope"]

        return all_bindings

    # ==================== 工具方法 ====================

    @staticmethod
    def _fuzzy_title_match(hint: str, title: str) -> bool:
        """模糊标题匹配：兼容 fixed_template 标题变体。"""
        normalized_hint = MaterialMatcher._normalize_match_title(hint)
        normalized_title = MaterialMatcher._normalize_match_title(title)
        if not normalized_hint or not normalized_title:
            return False
        if (
            normalized_hint in normalized_title
            or normalized_title in normalized_hint
        ):
            return True
        hint_kws = MaterialMatcher._extract_match_keywords(normalized_hint)
        title_kws = MaterialMatcher._extract_match_keywords(normalized_title)
        if hint_kws & title_kws:
            return True
        return len(hint_kws & title_kws) >= 2

    @staticmethod
    def _normalize_match_title(text: str) -> str:
        normalized = strip_existing_number((text or "").strip()).lower()
        normalized = re.sub(r"[（()）【】\[\]\s]", "", normalized)
        normalized = normalized.replace("及", "").replace("与", "").replace("和", "")
        for suffix in ("格式", "模板", "表格", "文件", "内容"):
            normalized = normalized.replace(suffix, "")
        return normalized

    @staticmethod
    def _extract_match_keywords(text: str) -> set[str]:
        keywords = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
        compound_terms = (
            "投标函", "投标函附录", "附录", "开标一览表",
            "报价书", "报价表", "授权委托书", "授权书",
            "声明函", "承诺书",
        )
        for term in compound_terms:
            if term in text:
                keywords.add(term)
        return keywords

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    @staticmethod
    def _parse_profile(node: WritingOutlineNode) -> Optional[dict]:
        if not node.chapter_profile:
            return None
        try:
            return json.loads(node.chapter_profile)
        except (json.JSONDecodeError, TypeError):
            return None

    # ==================== DB 读写 ====================

    # 与覆盖统计口径单一同源。matcher 不匹配的 sub_type 集合 ==
    # 覆盖统计不计入分母的 sub_type 集合, 由 HIDDEN_COVERAGE_SUB_TYPES 权威定义。
    _EXCLUDED_SUB_TYPES = HIDDEN_COVERAGE_SUB_TYPES

    async def _load_confirmed_clauses(
        self, project_id: int
    ) -> list[WritingClause]:
        result = await self.db.execute(
            select(WritingClause).where(
                WritingClause.project_id == project_id,
                WritingClause.confirmed == 1,
                WritingClause.sub_type.notin_(self._EXCLUDED_SUB_TYPES),
            ).order_by(WritingClause.sort_order)
        )
        return [
            clause for clause in result.scalars().all()
            if is_body_consumable_clause(clause)
        ]

    async def _load_outline_nodes(
        self, project_id: int
    ) -> list[WritingOutlineNode]:
        result = await self.db.execute(
            select(WritingOutlineNode).where(
                WritingOutlineNode.project_id == project_id,
            ).order_by(WritingOutlineNode.depth, WritingOutlineNode.sort_order)
        )
        return list(result.scalars().all())

    async def _load_materials(
        self, project_id: int
    ) -> list[WritingMaterial]:
        result = await self.db.execute(
            select(WritingMaterial).where(
                WritingMaterial.project_id == project_id,
                WritingMaterial.processing_status == "completed",
            )
        )
        return [
            material
            for material in result.scalars().all()
            if str(getattr(material, "material_role", "normal") or "normal") != "split_parent"
            and str(getattr(material, "split_review_status", "none") or "none") != "ignored"
        ]

    async def _save_bindings(
        self,
        project_id: int,
        raw_bindings: list[dict],
    ) -> list[WritingBinding]:
        # 写入职责下沉到 binding_persistence，本方法保持薄转发。
        # 删除口径（保留人工 confirmed/rejected、仅重算 suggested）由
        # prepare_binding_rematch 在任务入口统一处理；此处只做"跳过与存活人工
        # 决策重复的候选 + 写入"，避免被拒绝的错误边随重匹配复活或产生重复边。
        # 写入守卫（取消墓碑 + projects 父行存在校验）在持久化模块内延续。
        # is_project_cancelled 在调用时按模块级名称解析，保持可被测试 monkeypatch。
        deps = BindingPersistenceDeps(
            is_project_cancelled=is_project_cancelled,
            auto_confirm_threshold=_AUTO_CONFIRM_THRESHOLD,
        )
        return await persist_rematched_bindings(
            self.db, project_id, raw_bindings, deps
        )


def get_material_matcher(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None,
) -> MaterialMatcher:
    """工厂函数"""
    return MaterialMatcher(db=db, llm_client=llm_client, project_id=project_id)
