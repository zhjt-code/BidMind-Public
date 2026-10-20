"""SectionComposer 草稿落库子模块（Task 6 Step 2）

仅承载一个职责：``save_draft`` 把章节正文持久化为
``writing_section_drafts`` 新版本，并兼任 的占位符出口闸门。

设计要点：
- 接收 ``composer`` 引用以复用 ``composer.db`` / ``composer._db_lock``
 与 ``composer._serialize_source_ids`` thin forwarder。
- 与原 ``SectionComposer._save_draft`` 行为字节级一致：
 - sanitize 触发后 logger.warning 字段顺序、replaced_count 计算式不变；
 - is_current 翻转、version 自增、content_format / status / is_user_edited
 / is_locked 默认值与字段写入顺序保持原状；
 - 锁定 ``async with composer._db_lock`` 串行化策略。
- ``_PLACEHOLDER_FALLBACK_RE`` / ``_PLACEHOLDER_WHITELIST_MAP`` 仍由主模块
 ``section_composer`` 持有；本模块通过函数级 import 读取，避免循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from typing import TYPE_CHECKING, Optional, Union

from loguru import logger
from sqlalchemy import select

from app.core.xml_compat import sanitize_xml_compatible_text
from app.core.writer.composer.length_control import count_plain_text
from app.models.writing_outline_node import OutlineNodeStatus
from app.models.writing_section_draft import DraftStatus, WritingSectionDraft

if TYPE_CHECKING:
    from app.core.writer.section_composer import SectionComposer
    from app.models.writing_outline_node import WritingOutlineNode


def classify_draft_failure_reason(content: str | None) -> str | None:
    """识别不能视为成功草稿的空产出/失败占位。"""
    if not content:
        return "章节生成结果为空"
    visible_text = re.sub(r"<[^>]+>", "", content)
    visible_text = unescape(re.sub(r"\s+", "", visible_text))
    if not visible_text:
        return "章节生成结果为空"
    if "内容生成失败，请手动补充" in visible_text:
        return "章节生成失败占位内容"
    return None


def _derive_gate_review_state(gate_flags: Optional[dict]) -> tuple[str | None, str | None]:
    """从 Gate/CoVe patch 中提取需要人工复核的状态。"""
    if not isinstance(gate_flags, dict):
        return None, None

    review_patch_keys = {
        "low_confidence",
        "material_trace_missing",
        "placeholder_density_violated",
        "table_fill_rate_violated",
        "unbound_material_ids",
    }
    for gate_name, entry in gate_flags.items():
        if not isinstance(entry, dict):
            continue
        patch = entry.get("patch")
        if not isinstance(patch, dict):
            continue
        state = patch.get("failure_state")
        if state in {"failed", "needs_review"}:
            reason = str(entry.get("reason") or gate_name or state)
            return str(state), reason[:500]
        if any(patch.get(key) not in (None, False, "", [], {}) for key in review_patch_keys):
            reason = str(entry.get("reason") or gate_name or "Gate 标记需人工复核")
            return "needs_review", reason[:500]
    return None, None


@dataclass
class DraftWriteRequest:
    """
    职责: 收敛草稿正文写入参数。
    不做: 不生成摘要、不跑 Gate/Judge、不渲染 Word。
    """

    content: str
    source_material_ids: Optional[list[int]] = None
    source_clause_ids: Optional[list[int]] = None
    gate_flags: Optional[dict] = None
    section_plan_id: Optional[int] = None
    planning_mode: Optional[str] = None
    planning_failure_code: Optional[str] = None
    update_node_status: bool = True


def as_draft_write_request(
    content: Union[str, DraftWriteRequest],
    **kwargs: object,
) -> DraftWriteRequest:
    if isinstance(content, DraftWriteRequest):
        return content
    return DraftWriteRequest(content=content, **kwargs)  # type: ignore[arg-type]


async def save_draft(
    composer: "SectionComposer",
    node: "WritingOutlineNode",
    content: Union[str, DraftWriteRequest],
    *,
    source_material_ids: Optional[list[int]] = None,
    source_clause_ids: Optional[list[int]] = None,
    gate_flags: Optional[dict] = None,
    section_plan_id: Optional[int] = None,
    planning_mode: Optional[str] = None,
    planning_failure_code: Optional[str] = None,
) -> WritingSectionDraft:
    """保存草稿（新版本，旧版本标记为非当前）。

 P2.1b: gate_flags 来自 _run_writer_post_llm_gate 的聚合结果, 为
 None 表示所有 gate pass 或未启用; 前端消费此字段展示单章橙标。: 落库前统一调用 sanitize_residual_placeholders 作为出口闸门,
 覆盖 fixed_template/data_fill/free_writing/失败兜底全部路径,
 防止 LLM 从素材复读的 {xxx} 占位符泄漏到 Word/HTML 渲染层。
 """
    request = as_draft_write_request(
        content,
        source_material_ids=source_material_ids,
        source_clause_ids=source_clause_ids,
        gate_flags=gate_flags,
        section_plan_id=section_plan_id,
        planning_mode=planning_mode,
        planning_failure_code=planning_failure_code,
    )
    payload = request.content
    # 函数级 import 避免与主模块的循环依赖
    from app.core.writer.section_composer import (
        _PLACEHOLDER_FALLBACK_RE,
        _PLACEHOLDER_WHITELIST_MAP,
        sanitize_residual_placeholders,
    )

    if payload:
        payload = sanitize_xml_compatible_text(payload)
        sanitized = sanitize_residual_placeholders(payload)
        if sanitized != payload:
            replaced_count = len(_PLACEHOLDER_FALLBACK_RE.findall(payload)) + sum(
                payload.count(k) for k in _PLACEHOLDER_WHITELIST_MAP
            )
            logger.warning(
                "writer placeholder gate: node_id={} title={!r} replaced={} ",
                node.id,
                node.title,
                replaced_count,
            )
            payload = sanitized
    word_count = count_plain_text(payload)
    failure_reason = classify_draft_failure_reason(payload)
    gate_failure_state, gate_failure_reason = _derive_gate_review_state(request.gate_flags)
    final_failure_state = "failed" if failure_reason else (gate_failure_state or "completed")
    final_failure_reason = failure_reason or gate_failure_reason
    final_review_required = 1 if final_failure_state in {"failed", "needs_review"} else 0
    previous_status = getattr(node, "status", None)
    async with composer._db_lock:
        existing = await composer.db.execute(
            select(WritingSectionDraft).where(
                WritingSectionDraft.outline_node_id == node.id,
                WritingSectionDraft.is_current == 1,
            )
        )
        old_drafts = list(existing.scalars().all())
        max_version = 0
        for d in old_drafts:
            d.is_current = 0
            if d.version > max_version:
                max_version = d.version

        draft = WritingSectionDraft(
            outline_node_id=node.id,
            version=max_version + 1,
            section_plan_id=request.section_plan_id,
            planning_mode=request.planning_mode,
            planning_failure_code=request.planning_failure_code,
            content=payload,
            content_format="html",
            source_clause_ids=composer._serialize_source_ids(request.source_clause_ids),
            source_material_ids=composer._serialize_source_ids(request.source_material_ids),
            is_current=1,
            is_user_edited=0,
            is_locked=0,
            word_count=word_count,
            status=DraftStatus.draft,
            failure_state=final_failure_state,
            failure_reason=final_failure_reason,
            review_required=final_review_required,
            gate_flags=request.gate_flags,
        )
        composer.db.add(draft)
        if request.update_node_status:
            node.status = OutlineNodeStatus.draft
        try:
            await composer.db.commit()
            await composer.db.refresh(draft)
        except Exception:
            if request.update_node_status:
                node.status = previous_status
            from app.core.writer.composer.compose_metrics import writer_compose_commit_total

            writer_compose_commit_total.labels(kind="draft_body", outcome="error").inc()
            raise
    from app.core.writer.composer.compose_metrics import writer_compose_commit_total

    writer_compose_commit_total.labels(kind="draft_body", outcome="ok").inc()
    cache = getattr(composer, "_compose_cache", None)
    if cache is not None:
        cache.invalidate_current_draft(node.id)
        cache.set_current_draft(node.id, draft)
    return draft
