"""招标要求服务

处理招标要求的增删改查、批量确认、提取触发等核心业务逻辑。
路由层委托此服务执行，本模块不包含任何 HTTP 语义。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ServiceError
from app.core.extractor.scoring.operational_assessment import (
    attach_suspected_assessment_payload,
    is_suspected_assessment_meta,
)
from app.core.extractor.scoring.score_totals import (
    ScoringTotalsEntry,
    build_totals_report_payload,
)
from app.core.extractor.scoring.scoring_green_verdict import resolve_scoring_verdict
from app.core.review_contracts import (
    actionable_requirement_condition,
    is_scoring_universe_member,
    parse_json_list,
    serialize_json_list,
)
from app.core.tasks import (
    TASK_TERMINAL_STAGES,
    get_extract_task_name,
    is_task_active,
    run_extract_task,
)
from app.core.tasks.extract_progress import (
    EXTRACT_PROGRESS_STEPS,
    step_index_for_stage,
)
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus
from app.models.requirement import Requirement, RequirementCategory
from app.models.user import User
from app.schemas.requirement import RequirementCreate, RequirementUpdate
from app.services.audit_service import audit_log
from app.services.liveness.extract_liveness import (
    extract_heartbeat_is_midstage,
    inspect_extract_task_liveness,
    persist_extract_task_dead,
    should_declare_extract_dead,
)
from app.services.project_access import get_project_or_raise
from app.services.requirement_grouping import group_requirements_for_display
from app.services.scoring_ledger_report_service import build_scoring_ledger_report


def _sync_score_weight_to_metadata(
    score_weight: float,
    meta: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """评分项满分修正辅助：同步 scoring_metadata.max_score 与新满分值，保留其他字段。

    职责: 将 max_score 更新为 score_weight，不修改其他字段。
    不做: 不写数据库，不校验 score_weight > 0（由 schema 层校验）。
    """
    result: dict[str, Any] = dict(meta) if meta else {}
    result["max_score"] = score_weight
    return result


def _read_heartbeat_json(
    heartbeat: Optional[dict[str, Any]],
    key: str,
    expected_type: type,
) -> Any:
    """从心跳数据中安全读取 JSON 字段（兼容 Redis JSON 字符串）。

    职责: 按 expected_type(list/dict) 归一化心跳字段的三种形态——原生类型、
          JSON 字符串、缺失/异型。
    不做: 不统一两者的兜底语义——expected_type 为 list 时, 无法解析为 list 的
          非空字符串按 [raw] 单元素兜底(保留告警原文, 原 _read_extraction_warnings
          行为); 为 dict 时解析失败/异型一律返回 None(原 _read_extraction_quality
          行为)。
    """
    empty: Any = [] if expected_type is list else None
    raw = heartbeat.get(key) if heartbeat else None
    if raw is None:
        return empty
    if isinstance(raw, expected_type):
        return raw
    if isinstance(raw, str):
        import json as _json
        try:
            parsed = _json.loads(raw)
        except (ValueError, TypeError):
            if expected_type is list:
                return [raw] if raw else []
            return None
        if isinstance(parsed, expected_type):
            return parsed
        return [raw] if expected_type is list else None
    return empty


def _read_extraction_warnings(heartbeat: Optional[dict[str, Any]]) -> list[str]:
    """从心跳数据中安全读取 extraction_warnings（兼容 Redis JSON 字符串）。"""
    return _read_heartbeat_json(heartbeat, "extraction_warnings", list)


def _read_extraction_quality(heartbeat: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """从心跳数据中安全读取 extraction_quality（兼容 Redis JSON 字符串）。"""
    return _read_heartbeat_json(heartbeat, "extraction_quality", dict)


def _build_scoring_totals_report(
    requirements: list[Requirement],
) -> Optional[dict[str, Any]]:
    """确认页"按卷满分校验"报告（第3层人工兜底）。

 职责: 从评分项 score_weight + scoring_metadata(volume/卷权重/卷满分)重建卷定义,
 跑按卷满分校验, 供确认页提示人工核对(某卷满分缺口/卷权重和≠100%)。
 不做: 不修改任何数据; 无评分项时返回 None(确认页不展示横幅)。

 满分全集: 与结果页聚合同口径——纳入 scoring_item 与 reconcile 回填到 normal
 主行的重点评审项(is_key_review 且 score_weight>0), 否则技术卷会少算回填满分(60 而非 82)。
 """
    scoring = [
        r for r in requirements
        if is_scoring_universe_member(
            requirement_kind=getattr(r, "requirement_kind", "normal"),
            is_key_review=getattr(r, "is_key_review", 0),
            max_score=getattr(r, "score_weight", None),
        )
    ]
    entries: list[ScoringTotalsEntry] = []
    parse_diagnostics: list[str] = []
    suspected_count = 0
    suspected_score = 0.0
    for r in scoring:
        meta = r.scoring_metadata or {}
        raw_diagnostics = meta.get("parse_diagnostics")
        if isinstance(raw_diagnostics, list):
            parse_diagnostics.extend(str(item) for item in raw_diagnostics if str(item).strip())
        elif raw_diagnostics:
            parse_diagnostics.append(str(raw_diagnostics))
        rule_texts: list[Any] = []
        for rule_key in ("score_rules", "deduction_rules"):
            raw_rules = meta.get(rule_key)
            if isinstance(raw_rules, list):
                rule_texts.extend(raw_rules)
            elif raw_rules:
                rule_texts.append(raw_rules)
        has_rules = any(str(rule).strip() for rule in rule_texts)
        # 阶段3: penalty/bonus 调整项不参与满分核计与缺失统计(零分值是其正常形态)
        if str(meta.get("fact_type") or "base_score") in ("penalty", "bonus"):
            continue
        if is_suspected_assessment_meta(meta):
            suspected_count += 1
            try:
                suspected_score += float(r.score_weight or 0)
            except (TypeError, ValueError):
                pass
            continue
        raw_description = getattr(r, "description", None) if hasattr(r, "description") else None
        description_length = (
            len(str(raw_description or "").strip()) if hasattr(r, "description") else None
        )
        entries.append(
            ScoringTotalsEntry(
                score_weight=float(r.score_weight or 0),
                volume=meta.get("volume"),
                volume_label=meta.get("volume_label"),
                lot=meta.get("lot"),
                scheme=meta.get("scheme"),
                volume_weight=meta.get("volume_weight"),
                volume_max=meta.get("volume_max"),
                source_page=getattr(r, "source_page", None),
                has_rules=has_rules,
                description_length=description_length,
                is_llm_source=bool(meta.get("fidelity_flag"))
                or str(meta.get("source") or "").lower() in {"llm", "llm_only"},
                diagnostic_name=str(
                    meta.get("score_item_name") or raw_description or ""
                )[:80],
            )
        )
    payload = build_totals_report_payload(
        entries,
        extra_diagnostics=list(dict.fromkeys(parse_diagnostics)),
    )
    if payload is None:
        if suspected_count <= 0:
            return None
        payload = {
            "needs_review": True,
            "note": "未识别到带分值的有效评分项",
            "by_volume": {},
            "volume_checks": {},
            "weight_sum_ok": True,
            "item_count": 0,
            "missing_score_count": 0,
            "diagnostics": [],
        }
    labels = {
        "technical": "技术标",
        "price": "价格",
        "commercial": "商务标",
        "qualification": "资格",
        "staffing": "人员",
        "performance": "业绩",
        "other": "其他",
        "unspecified": "其他",
    }
    for field in ("by_volume", "volume_checks"):
        raw = payload.get(field)
        if isinstance(raw, dict):
            payload[field] = {labels.get(key, key): value for key, value in raw.items()}
    return attach_suspected_assessment_payload(
        payload, count=suspected_count, score=suspected_score
    )


# 步骤定义收口于 app.core.tasks.extract_progress(与心跳权重、前端步骤映射同源),
# 本模块只负责把契约渲染成状态接口的 steps[]。
_EXTRACT_STEP_CONFIG = EXTRACT_PROGRESS_STEPS

# "提取已完整落库"的业务终态集合——`run_extract_task` 走完全部阶段
# (普通要求+评分项+跨通道归并+警告收集)才提交 EXTRACTED, 其后的 REVIEWING/
# COMPLETED 同样意味着提取早已完成。完成态只认业务状态, 不认心跳新鲜度。
_EXTRACT_SETTLED_STATUSES = frozenset(
    {ProjectStatus.EXTRACTED, ProjectStatus.REVIEWING, ProjectStatus.COMPLETED}
)


def _build_requirement_extract_steps(
    stage: str,
    progress: int,
    message: str,
    status: str,
) -> list[dict[str, Any]]:
    if status == "completed":
        return [
            {
                "key": step.key,
                "label": step.label,
                "status": "completed",
                "progress": 100,
                "detail": None,
            }
            for step in _EXTRACT_STEP_CONFIG
        ]

    steps: list[dict[str, Any]] = []
    active_idx = step_index_for_stage(stage)

    if active_idx == -1 and progress > 0:
        for idx in range(len(_EXTRACT_STEP_CONFIG) - 1, -1, -1):
            if progress >= _EXTRACT_STEP_CONFIG[idx].start:
                active_idx = idx
                break

    for idx, step in enumerate(_EXTRACT_STEP_CONFIG):
        range_start, range_end = step.start, step.end
        step_progress = 0
        if range_end > range_start:
            step_progress = int(
                max(0, min((progress - range_start) / (range_end - range_start), 1)) * 100
            )
        elif progress >= range_end:
            step_progress = 100

        is_current = idx == active_idx and status == "running"
        if status == "failed" and idx == active_idx:
            step_status = "failed"
        elif is_current:
            # 当前步即使触达 band 终点也保持 running；完成由 stage 前移背书。
            step_status = "running"
        elif active_idx != -1:
            # 已知当前步时前序完成只认秩, overall 落后/超前都不得改写已越过或未到达的步骤。
            if idx < active_idx:
                step_status = "completed"
                step_progress = 100
            else:
                step_status = "waiting"
        elif progress >= range_end and status != "failed":
            step_status = "completed"
            step_progress = 100
        else:
            step_status = "waiting"

        steps.append(
            {
                # key 是前端绑定步骤的稳定标识, label 仅用于展示(教训:
                # 按数组下标对位会在步骤增减时静默错位)。
                "key": step.key,
                "label": step.label,
                "status": step_status,
                "progress": step_progress if step_status == "running" else (100 if step_status == "completed" else 0),
                "detail": message if step_status == "running" and message else None,
            }
        )

    return steps


def _extract_status_payload(
    *,
    status: str,
    stage: str,
    progress: int,
    message: str,
    count: int,
    steps_message: Optional[str] = None,
    error_message: Optional[str] = None,
    warnings: Optional[list[str]] = None,
    quality: Optional[dict[str, Any]] = None,
    heartbeat: Optional[dict[str, Any]] = None,
    include_run_meta: bool = False,
) -> dict[str, Any]:
    """get_extract_status 各分支响应字典的统一构造器。

 职责: 按既有响应契约拼装状态载荷; overall_status/overall_progress 恒等于
 status/progress, steps 交由 _build_requirement_extract_steps 渲染
 (steps_message 缺省复用 message, 仅 completed 分支的步骤文案不同)。
 不做: 不承担"何时返回哪段"的完成态/失败态判定(判定链在
 get_extract_status 内保持原样); include_run_meta=False 时不产出
 run_id/revision 键——failed/idle 分支历史契约无这两个键, 禁止悄悄补齐。
 """
    payload: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "progress": progress,
        "message": message,
        "count": count,
        "overall_status": status,
        "overall_progress": progress,
        "steps": _build_requirement_extract_steps(
            stage=stage,
            progress=progress,
            message=message if steps_message is None else steps_message,
            status=status,
        ),
        "error_message": error_message,
        "is_recovering": False,
        "recovery_count": 0,
        "extraction_warnings": [] if warnings is None else warnings,
        "extraction_quality": quality,
    }
    if include_run_meta:
        payload["run_id"] = (heartbeat or {}).get("run_id")
        payload["revision"] = (heartbeat or {}).get("revision")
    return payload


class RequirementService:
    """招标要求服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _get_project_or_raise(
        self,
        project_id: int,
        user: Optional[User] = None,
        *,
        require_owner: bool = False,
    ) -> Project:
        """获取项目并校验权限（委托 project_access 共享实现，S-07）"""
        return await get_project_or_raise(
            self.db, project_id, user, require_owner=require_owner
        )

    async def _get_tender_doc(self, project_id: int) -> Optional[Document]:
        """获取项目的招标文档"""
        result = await self.db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def list_requirements(
        self,
        project_id: int,
        user: User,
        confirmed: Optional[bool] = None,
        category: Optional[str] = None,
    ) -> dict[str, Any]:
        """查询项目的招标要求列表"""
        await self._get_project_or_raise(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            return {
                "total": 0,
                "confirmed_count": 0,
                "pending_count": 0,
                "items": [],
                "scoring_totals_report": None,
                "scoring_ledger_report": None,
                "scoring_verdict": None,
            }

        query = select(Requirement).where(
            Requirement.document_id == tender_doc.id
        )
        if confirmed is not None:
            query = query.where(Requirement.confirmed == (1 if confirmed else 0))
        if category:
            query = query.where(Requirement.category == category)

        result = await self.db.execute(query.order_by(Requirement.id))
        requirements = result.scalars().all()

        # 展示分组：把复合拆分子面（同分类+同描述、仅 sub_requirements 不同的多行）
        # 折叠为单一代表项，消除人工确认页的视觉重复。底层行不变，
        # 统计口径同步切到"组"维度，确保页面条数与 total/confirmed 一致。
        grouped_items = group_requirements_for_display(list(requirements))

        total = len(grouped_items)
        confirmed_count = sum(1 for item in grouped_items if item["confirmed"])
        pending_count = total - confirmed_count

        logger.debug(
            f"查询招标要求: project_id={project_id}, rows={len(requirements)}, groups={total}"
        )

        totals_payload = _build_scoring_totals_report(list(requirements))
        ledger_payload, ledger_core = await build_scoring_ledger_report(
            self.db, tender_doc.id, list(requirements)
        )
        return {
            "total": total,
            "confirmed_count": confirmed_count,
            "pending_count": pending_count,
            "items": grouped_items,
            # 第3层: 按卷满分校验报告(评分项卷满分缺口/权重和异常→确认页提示人工核对)
            "scoring_totals_report": totals_payload,
            # 换轴P5-1: 账本对账报告(原文组标签轴三态; 冻结窗口内与legacy报告并存展示)
            "scoring_ledger_report": ledger_payload,
            # 横幅三态唯一权威源(与提取侧 VLM 触发同公式); None=无评分数据不渲染
            "scoring_verdict": resolve_scoring_verdict(totals_payload, ledger_core),
        }

    async def create(
        self,
        project_id: int,
        data: RequirementCreate,
        user: User,
    ) -> Requirement:
        """手动添加招标要求"""
        await self._get_project_or_raise(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise ServiceError("项目没有招标文档，请先上传招标文件")

        requirement = Requirement(
            project_id=project_id,
            document_id=tender_doc.id,
            category=RequirementCategory[data.category],
            description=data.description,
            is_mandatory=1 if data.is_mandatory else 0,
            source_page=data.source_page or (data.source_pages[0] if data.source_pages else None),
            source_pages=serialize_json_list(data.source_pages),
            keywords=serialize_json_list(data.keywords),
            sub_requirements=serialize_json_list(data.sub_requirements),
            merged_count=data.merged_count,
            topic=data.topic,
            subject_scope=data.subject_scope,
            canonical_key=data.canonical_key,
            main_requirement_id=data.main_requirement_id,
            confirmed=0,
        )
        self.db.add(requirement)
        await self.db.flush()
        await self.db.refresh(requirement)

        logger.info(
            f"手动添加招标要求: id={requirement.id}, project_id={project_id}"
        )
        return requirement

    async def get(
        self,
        project_id: int,
        requirement_id: int,
        user: User,
    ) -> Requirement:
        """获取单条招标要求"""
        await self._get_project_or_raise(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise NotFoundError(f"招标要求不存在: id={requirement_id}")

        result = await self.db.execute(
            select(Requirement).where(
                Requirement.id == requirement_id,
                Requirement.document_id == tender_doc.id,
            )
        )
        requirement = result.scalar_one_or_none()
        if not requirement:
            raise NotFoundError(f"招标要求不存在: id={requirement_id}")
        return requirement

    async def update(
        self,
        project_id: int,
        requirement_id: int,
        update_data: RequirementUpdate,
        user: User,
    ) -> Requirement:
        """更新招标要求（人工确认时使用）"""
        await self._get_project_or_raise(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise NotFoundError(f"招标要求不存在: id={requirement_id}")

        result = await self.db.execute(
            select(Requirement).where(
                Requirement.id == requirement_id,
                Requirement.document_id == tender_doc.id,
            )
        )
        requirement = result.scalar_one_or_none()
        if not requirement:
            raise NotFoundError(f"招标要求不存在: id={requirement_id}")

        data = update_data.model_dump(exclude_unset=True)
        for field, value in data.items():
            if field == "confirmed" and isinstance(value, bool):
                value = 1 if value else 0
            if field in {"source_pages", "keywords", "sub_requirements"}:
                if field == "source_pages":
                    pages = value or []
                    requirement.source_page = pages[0] if pages else None
                value = serialize_json_list(value)
            setattr(requirement, field, value)

        # P2.1: 人工升降级归因——评审意义由人工修改时标记 manual,
        # 升为审查项(actionable)后归因清空（不再展示折叠原因徽标）
        if "review_relevance" in data:
            requirement.relevance_reason = (
                None if data["review_relevance"] == "actionable" else "manual"
            )

        if "score_weight" in data and data["score_weight"] is not None:
            requirement.scoring_metadata = _sync_score_weight_to_metadata(
                float(data["score_weight"]), requirement.scoring_metadata
            )

        if "description" in data:
            from app.core.extractor.tender_extractor import (
                infer_canonical_key,
                infer_subject_scope,
                infer_topic,
            )

            new_desc = requirement.description
            keywords = [
                str(keyword)
                for keyword in parse_json_list(getattr(requirement, "keywords", None))
                if keyword is not None
            ]
            requirement.subject_scope = infer_subject_scope(new_desc, keywords)
            requirement.topic = infer_topic(new_desc, keywords)
            requirement.canonical_key = infer_canonical_key(new_desc, keywords)

        await self.db.flush()
        await self.db.refresh(requirement)

        logger.info(
            f"更新招标要求: id={requirement_id}, fields={list(data.keys())}"
        )
        await audit_log(
            self.db, user_id=user.id, username=user.username,
            category="bid_review",
            action="update", resource_type="requirement", resource_id=requirement_id,
            detail=f"更新字段: {list(data.keys())}",
        )
        return requirement

    async def confirm_batch(
        self,
        project_id: int,
        requirement_ids: list[int],
        user: User,
    ) -> int:
        """批量确认招标要求"""
        await self._get_project_or_raise(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise ServiceError("项目没有招标文档")

        result = await self.db.execute(
            select(Requirement).where(
                Requirement.id.in_(requirement_ids),
                Requirement.document_id == tender_doc.id,
            )
        )
        requirements = result.scalars().all()

        if len(requirements) != len(requirement_ids):
            raise ServiceError("部分招标要求不存在或不属于该项目")

        for req in requirements:
            req.confirmed = 1

        await self.db.flush()

        count = len(requirements)
        logger.info(
            f"批量确认招标要求: project_id={project_id}, count={count}"
        )
        await audit_log(
            self.db, user_id=user.id, username=user.username,
            category="bid_review",
            action="confirm", resource_type="requirement", resource_id=project_id,
            detail=f"批量确认 {count} 条招标要求",
        )
        return count

    async def confirm_all(
        self,
        project_id: int,
        user: User,
    ) -> int:
        """确认所有未确认的招标要求"""
        await self._get_project_or_raise(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise ServiceError("项目没有招标文档")

        # 可评审性闸门: 全量确认仅作用于可进入审查的条款
        # （actionable / 评分项 / 重点评审项），参考条款保持未确认、不进入审查，
        # 用户可在折叠区"升为审查项"后再随确认流生效。
        query = select(Requirement).where(
            Requirement.document_id == tender_doc.id,
            Requirement.confirmed == 0,
        )
        gate_condition = actionable_requirement_condition()
        if gate_condition is not None:
            query = query.where(gate_condition)
        result = await self.db.execute(query)
        requirements = result.scalars().all()

        for req in requirements:
            req.confirmed = 1

        await self.db.flush()

        count = len(requirements)
        logger.info(
            f"确认所有招标要求: project_id={project_id}, count={count}"
        )
        await audit_log(
            self.db, user_id=user.id, username=user.username,
            category="bid_review",
            action="confirm", resource_type="requirement", resource_id=project_id,
            detail=f"确认全部 {count} 条招标要求",
        )
        return count

    async def delete(
        self,
        project_id: int,
        requirement_id: int,
        user: User,
    ) -> None:
        """删除单条招标要求"""
        await self._get_project_or_raise(project_id, user, require_owner=True)

        tender_doc = await self._get_tender_doc(project_id)
        if not tender_doc:
            raise NotFoundError(f"招标要求不存在: id={requirement_id}")

        result = await self.db.execute(
            select(Requirement).where(
                Requirement.id == requirement_id,
                Requirement.document_id == tender_doc.id,
            )
        )
        requirement = result.scalar_one_or_none()
        if not requirement:
            raise NotFoundError(f"招标要求不存在: id={requirement_id}")

        await self.db.delete(requirement)
        await self.db.flush()

        logger.info(
            f"删除招标要求: id={requirement_id}, project_id={project_id}"
        )
        await audit_log(
            self.db, user_id=user.id, username=user.username,
            category="bid_review",
            action="delete", resource_type="requirement", resource_id=requirement_id,
        )

    async def extract(
        self,
        project_id: int,
        user: User,
    ) -> dict[str, Any]:
        """触发招标要求提取后台任务"""
        project = await self._get_project_or_raise(project_id, user)

        doc_result = await self.db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
                Document.parsed == 1,
            )
        )
        tender_doc = doc_result.scalar_one_or_none()
        if not tender_doc:
            raise ServiceError("项目没有已解析的招标文档，请先上传并处理招标文件")

        # 投标信封口径: 兼容整本单文档与分卷多文档, 禁止 scalar_one 单行假设
        from app.core.bid_envelope import load_bid_envelope

        bid_envelope = await load_bid_envelope(self.db, project_id)
        if not bid_envelope.has_documents:
            raise ServiceError("项目缺少投标文档，请先上传投标文件")
        if not bid_envelope.is_complete:
            raise ServiceError(
                "投标文件未齐套（分卷模式需商务标与技术标各一份），请返回上传页补齐"
            )

        existing_result = await self.db.execute(
            select(func.count(Requirement.id)).where(
                Requirement.document_id == tender_doc.id
            )
        )
        existing_count = existing_result.scalar() or 0

        if existing_count > 0:
            logger.info(
                f"项目 {project_id} 已有 {existing_count} 条招标要求，跳过提取"
            )
            return {
                "count": existing_count,
                "skipped": True,
                "status": "completed",
            }

        task_name = get_extract_task_name(project_id)

        if is_task_active(task_name):
            logger.info(f"项目 {project_id} 提取任务正在运行中")
            return {
                "count": 0,
                "skipped": True,
                "in_progress": True,
                "status": "running",
            }

        if project.status == ProjectStatus.EXTRACTING:
            logger.info(
                f"项目 {project_id} 状态为 EXTRACTING 但任务不活跃，重新启动"
            )

        # 要求提取是审查流程首个重 LLM 节点，必须与写标 trigger_extract
        # 同口径先预检建 hold，否则提取 token 落在基线之前永不计费。
        # pre_check 幂等：已有 HOLDING 复用，免费试用不重复占名额。
        from app.billing.services.balance_gate_error import raise_insufficient_balance
        from app.billing.services.billing_guard import BillingGuard
        from app.billing.services.wallet_service import InsufficientBalanceError
        try:
            await BillingGuard.pre_check(self.db, project, project.owner_id)
        except InsufficientBalanceError as balance_err:
            raise_insufficient_balance(balance_err)

        project.status = ProjectStatus.EXTRACTING
        await self.db.commit()

        from app.core.tasks import dispatch_background_job
        from app.core.tasks.extract_task import build_extract_dispatch_args

        task_name, generation, arq_args = build_extract_dispatch_args(project_id)
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="extract_requirements_job",
            fallback_factory=lambda: run_extract_task(
                project_id, generation=generation
            ),
            task_type="extract",
            project_id=project_id,
            arq_args=arq_args,
        )

        logger.info(
            f"招标要求提取任务已启动: project_id={project_id}, "
            f"queue_mode={queue_mode}, arq_job_id={arq_job_id}"
        )
        return {
            "count": 0,
            "skipped": False,
            "in_progress": True,
            "status": "started",
            "queue_mode": queue_mode,
            "run_id": generation.run_id,
            "revision": 0,
        }

    async def get_extract_status(
        self,
        project_id: int,
        user: User,
    ) -> dict[str, Any]:
        """获取招标要求提取进度"""
        project = await self._get_project_or_raise(project_id, user)

        tender_doc = await self._get_tender_doc(project_id)

        existing_count = 0
        if tender_doc:
            existing_result = await self.db.execute(
                select(func.count(Requirement.id)).where(
                    Requirement.document_id == tender_doc.id
                )
            )
            existing_count = existing_result.scalar() or 0

        task_name = get_extract_task_name(project_id)
        liveness = inspect_extract_task_liveness(task_name)
        heartbeat = liveness.heartbeat
        task_live = liveness.is_live

        # 完成态只认业务终态, 不认心跳新鲜度或落库条数。
        extract_settled = project.status in _EXTRACT_SETTLED_STATUSES
        failed_heartbeat = bool(heartbeat and heartbeat.get("stage") == "失败")
        if (
            existing_count > 0
            and not task_live
            and project.status != ProjectStatus.EXTRACTING
            and project.status != ProjectStatus.FAILED
            and not failed_heartbeat
            and (extract_settled or not extract_heartbeat_is_midstage(heartbeat))
        ):
            return _extract_status_payload(
                status="completed",
                stage="completed",
                progress=100,
                message=f"提取完成，共 {existing_count} 条",
                count=existing_count,
                steps_message="提取完成",
                warnings=_read_extraction_warnings(heartbeat),
                quality=_read_extraction_quality(heartbeat),
                heartbeat=heartbeat,
                include_run_meta=True,
            )

        # 执行体不在且工作未完成 → 写 FAILED。禁止假报 running / 回 idle。
        if should_declare_extract_dead(liveness, project.status):
            error_message = await persist_extract_task_dead(
                self.db,
                project,
                task_name=task_name,
                liveness=liveness,
            )
            return _extract_status_payload(
                status="failed",
                stage="failed",
                progress=0,
                message=error_message,
                count=0,
                error_message=error_message,
            )

        if not task_live:
            if failed_heartbeat:
                error_message = heartbeat.get("progress", "") if heartbeat else ""
                return _extract_status_payload(
                    status="failed",
                    stage="failed",
                    progress=0,
                    message=error_message,
                    count=0,
                    error_message=error_message,
                )
            if project.status == ProjectStatus.FAILED:
                error_message = project.error_message or "招标要求提取失败，请从失败节点继续重试"
                return _extract_status_payload(
                    status="failed",
                    stage="failed",
                    progress=0,
                    message=error_message,
                    count=0,
                    error_message=error_message,
                )
            if project.status != ProjectStatus.EXTRACTING:
                return _extract_status_payload(
                    status="idle",
                    stage="idle",
                    progress=0,
                    message="请先启动提取任务",
                    count=0,
                )

        stage = heartbeat.get("stage", "unknown") if heartbeat else "unknown"
        progress_str = (
            heartbeat.get("progress", "0%|处理中") if heartbeat else "0%|处理中"
        )
        if project.status == ProjectStatus.EXTRACTING:
            stage_key = str(stage).strip().lower()
            if stage_key in TASK_TERMINAL_STAGES or stage_key in {"失败", "unknown", ""}:
                stage = "启动"
                progress_str = "0%|重新提取已启动"

        progress = 0
        message = progress_str
        if "|" in progress_str:
            parts = progress_str.split("|", 1)
            try:
                progress = int(parts[0].replace("%", ""))
            except ValueError:
                progress = 0
            message = parts[1] if len(parts) > 1 else ""
        elif progress_str.endswith("%"):
            try:
                progress = int(progress_str.replace("%", ""))
            except ValueError:
                progress = 0

        return _extract_status_payload(
            status="running",
            stage=stage,
            progress=progress,
            message=message,
            count=0,
            warnings=_read_extraction_warnings(heartbeat),
            quality=_read_extraction_quality(heartbeat),
            heartbeat=heartbeat,
            include_run_meta=True,
        )
