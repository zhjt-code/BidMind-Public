"""审查流程业务服务

封装审查启动、进度查询、结果获取、报告生成等核心业务逻辑。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.ai.reviewer import get_review_orchestrator
from app.core.config import settings
from app.core.exceptions import ServiceError
from app.core.filename import build_export_filename
from app.core.report.generator import get_report_generator
from app.core.review_contracts import (
    REVIEW_STAGE_ASSEMBLING,
    REVIEW_STAGE_COMPLETED,
    REVIEW_STAGE_EXTRACTING,
    REVIEW_STAGE_FAILED,
    REVIEW_STAGE_PENDING,
    REVIEW_STAGE_RESULT_READY,
    REVIEW_STAGE_REVIEWING,
    REVIEW_STEP_LABEL_ASSEMBLE,
    REVIEW_STEP_LABEL_EXTRACT,
    REVIEW_STEP_LABEL_READY,
    REVIEW_STEP_LABEL_REVIEW,
    ReviewSummaryItem,
    build_review_summary,
    compute_estimated_score,
    compute_scoring_aggregate,
    counts_toward_review_summary,
    parse_json_list,
    parse_page_numbers,
    reviewable_requirement_conditions,
)
from app.core.tasks import (
    HEARTBEAT_TIMEOUT,
    MAX_RECOVERY,
    cancel_task,
    get_recovery_count,
    get_task_heartbeat,
    increment_recovery_count,
    inspect_task_liveness,
    reset_recovery_count,
    run_review_task,
)
from app.core.tasks.parallel_unit_progress import (
    heartbeat_belongs_to_running_step,
    heartbeat_phase_belongs_to_stage,
    heartbeat_text_matches_tokens,
    merge_running_step_detail,
    running_step_progress_from_heartbeat,
    stage_tokens_for_review_step,
)
from app.models.bid_response import BidResponse
from app.models.document import Document, DocumentType
from app.models.project import Project, ProjectStatus
from app.models.requirement import Requirement
from app.models.review_result import ReviewResult, RiskLevel
from app.models.user import User
from app.schemas.review import ReviewStatus, ReviewStepStatus
from app.services.audit_service import audit_log
from app.services.project_access import get_project_or_raise

_progress_cache: dict[int, dict[str, float]] = {}


# =============================================================================
# get_review_status 内部状态视图
# -----------------------------------------------------------------------------
# 拆方法重构 (2026-05): 把 540 行单方法拆为多步私有方法时, 用以下三个私有
# dataclass 收纳"中间状态", 避免方法签名爆炸成 10+ 参数。
# 仅本模块内部使用 (`_` 前缀), 不对外暴露契约。
# =============================================================================


@dataclass
class _ReviewCounts:
    """审查进度查询的原始计数."""

    total_requirements: int = 0
    extracted_responses: int = 0
    reviewed_items: int = 0


@dataclass
class _ReviewStageView:
    """get_review_status 阶段推断 + 自愈过程的可变中间状态.

    重构前对应 `stage` / `review_status` / `message` / `processed_items` /
    `progress` / `extraction_progress` / `review_progress` / `match_progress` /
    `result_progress` / `is_recovering` / `current_recovery_count` 等局部变量。
    """

    stage: str = ""
    review_status: str = ""
    message: str = ""
    processed_items: int = 0
    progress: float = 0.0
    extraction_progress: float = 0.0
    review_progress: float = 0.0
    match_progress: float = 0.0
    result_progress: float = 0.0
    is_recovering: bool = False
    current_recovery_count: int = 0


@dataclass
class _ReviewStepsView:
    """get_review_status 的 steps[] 派生结果 + overall 进度/状态."""

    steps: list = field(default_factory=list)
    overall_progress_pct: int = 0
    overall_status_str: str = "idle"
    result_ready: bool = False
    report_ready: bool = False
    assemble_progress_pct: int = 0
    assemble_detail: Optional[str] = None


async def _recheck_project_terminal(
    db: AsyncSession,
    project_id: int,
) -> tuple[ProjectStatus, str | None] | None:
    """恢复写入前重读项目终态, 避免 worker 收尾与 status 轮询竞态重投。"""
    result = await db.execute(
        select(Project.status, Project.error_message).where(Project.id == project_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    latest_status, error_message = row
    if latest_status in (ProjectStatus.COMPLETED, ProjectStatus.FAILED):
        return latest_status, error_message
    return None


def clear_review_cache(project_id: int) -> None:
    """清理指定项目的进度停滞缓存（项目删除时调用）"""
    _progress_cache.pop(project_id, None)


def calculate_review_summary(
    project_id: int, results: list[Any]
) -> dict[str, Any]:
    """基于统一合同计算审查汇总统计。"""
    summary_items = []
    for result in results:
        req = getattr(result, "requirement", None)
        # P2.2: 参考条款（非 actionable 且非评分链路）不计入汇总评分;
        # 人工降级后无需重审即从聚合排除。评分项铁律: scoring/key_review 恒计入。
        if req is not None and not counts_toward_review_summary(
            review_relevance=getattr(req, "review_relevance", None),
            requirement_kind=getattr(req, "requirement_kind", "normal"),
            is_key_review=getattr(req, "is_key_review", 0),
        ):
            continue
        category = ""
        if req is not None and getattr(req, "category", None):
            category = result.requirement.category.value
        elif getattr(result, "category", None):
            category = str(result.category)
        summary_items.append(
            ReviewSummaryItem(
                category=category,
                is_compliant=bool(result.is_compliant),
                compliance_level=result.compliance_level,
                risk_level=result.risk_level,
                score=result.score,
            )
        )
    summary = build_review_summary(project_id, summary_items)
    # P2 闭环：合并评分项预估总得分（按卷=技术标/商务标）
    scoring_views = []
    for r in results:
        req = getattr(r, "requirement", None)
        max_score = getattr(req, "score_weight", None)
        estimated = compute_estimated_score(
            structured_value=r.estimated_score,
            comment=getattr(r, "comment", ""),
            max_score=max_score,
            compliance_level=getattr(r, "compliance_level", None),
            applies=(
                getattr(req, "requirement_kind", "normal") == "scoring_item"
                or bool(getattr(req, "is_key_review", 0))
            ),
        )
        # 第2层: 按卷聚合需 volume/卷权重/卷满分(来自 scoring_metadata)
        meta = getattr(req, "scoring_metadata", None) or {}
        scoring_views.append({
            "requirement_kind": getattr(req, "requirement_kind", "normal"),
            # 满分全集口径需 is_key_review(纳入 reconcile 回填的 normal 主行)
            "is_key_review": bool(getattr(req, "is_key_review", 0)),
            "max_score": max_score,
            "estimated_score": estimated,
            "volume": meta.get("volume"),
            "volume_label": meta.get("volume_label"),
            "volume_weight": meta.get("volume_weight"),
            "volume_max": meta.get("volume_max"),
            # 多采购包文档按包独立核计, lot 缺失即聚合层 fail-visible
            "lot": meta.get("lot"),
        })
    summary.update(compute_scoring_aggregate(scoring_views))
    return summary


def _build_review_result_item(
    review_result: ReviewResult,
    *,
    category: str | None = None,
    subject_scope: str | None = None,
    main_requirement_id: int | None = None,
    source_pages: str | None = None,
    requirement_description: str | None = None,
    requirement_source_page: int | None = None,
    requirement_source_pages: str | None = None,
    requirement_keywords: str | None = None,
    requirement_sub_requirements: str | None = None,
    requirement_merged_count: int | None = None,
    requirement_kind: str | None = None,
    max_score: float | None = None,
    is_key_review: bool = False,
    scoring_metadata: dict | None = None,
    review_relevance: str | None = None,
    response_content: str | None = None,
    response_completeness: str | None = None,
    response_missing_elements: str | None = None,
    response_linked_to: int | None = None,
    response_evidence_anchor: str | None = None,
    retrieval_chunks: list[dict[str, Any]] | None = None,
    include_debug: bool = False,
) -> dict[str, Any]:
    """统一装配结果 API / 报告的结果项。

    Fix-06: 可选传入 ``retrieval_chunks``（来自 bid_response_chunks join，
    元素形如 ``{chunk_id, page_num, similarity, role}``）。用于前端"命中来源"视图
    与人工复核溯源，替代历史上只保留首个 ``source_chunk_id`` 的单点命中。

    A2: ``comment / requirement_summary / response_summary`` 等 LLM 自由文本
    字段在出口处统一过 ``sanitize_user_facing_text``，杜绝英文枚举裸出。
    """
    # A2: 出口闸门，避免 LLM 文本中的 inconclusive/unknown 等内部枚举裸出
    from app.core.ai.reviewer_parts.result_builder import is_failure_result
    from app.core.i18n import sanitize_user_facing_text as _sx
    # 脱敏会剥掉失败标记；待判定旗标必须在出口前按原始 comment 固化。
    pending_judgment = is_failure_result(review_result)
    item: dict[str, Any] = {
        "id": review_result.id,
        "project_id": review_result.project_id,
        "requirement_id": review_result.requirement_id,
        "bid_response_id": review_result.bid_response_id,
        "category": category,
        "requirement_summary": _sx(review_result.requirement_summary),
        "response_summary": _sx(review_result.response_summary),
        "is_compliant": bool(review_result.is_compliant),
        "compliance_level": (
            review_result.compliance_level.value
            if hasattr(review_result.compliance_level, "value")
            else str(review_result.compliance_level)
        ),
        "pending_judgment": pending_judgment,
        "score": review_result.score,
        "comment": _sx(review_result.comment),
        "risk_level": (
            review_result.risk_level.value
            if hasattr(review_result.risk_level, "value")
            else str(review_result.risk_level)
        ),
        "main_requirement_id": review_result.main_requirement_id or main_requirement_id,
        "subject_scope": subject_scope,
        "source_pages": source_pages,
        "source_page_numbers": parse_page_numbers(source_pages),
        "relevance_score": review_result.relevance_score,
        "relevance_note": review_result.relevance_note,
        "requirement_description": requirement_description,
        "requirement_source_page": requirement_source_page,
        "requirement_source_pages": parse_json_list(requirement_source_pages),
        "requirement_keywords": parse_json_list(requirement_keywords),
        "requirement_sub_requirements": parse_json_list(requirement_sub_requirements),
        "requirement_merged_count": requirement_merged_count,
        "requirement_kind": requirement_kind or "normal",
        "max_score": max_score,
        "is_key_review": bool(is_key_review),
        # P2.2: 要求当前评审意义(非 actionable 且非评分链路的明细行不计分,
        # 前端据此展示"参考条款·不计分"徽标)
        "review_relevance": review_relevance or "actionable",
        # 第2层: 卷归属与卷权重(供按卷加权折算与前端按卷展示)
        "volume": (scoring_metadata or {}).get("volume"),
        "volume_label": (scoring_metadata or {}).get("volume_label"),
        "volume_weight": (scoring_metadata or {}).get("volume_weight"),
        "volume_max": (scoring_metadata or {}).get("volume_max"),
        # P3-1: 分类依据(关键词/llm/manual; None=零命中未分类, 前端归未分类桶)
        "category_basis": (scoring_metadata or {}).get("category_basis"),
        # 采购包归属(多包文档按包聚合与前端按包下钻; 单包文档为 None)
        "lot": (scoring_metadata or {}).get("lot"),
        # 兜底(统一口径): 结构化缺失时按意见解析 + 不符合/缺失回退0, 保持徽标/详情/汇总与意见一致
        "estimated_score": compute_estimated_score(
            structured_value=review_result.estimated_score,
            comment=review_result.comment,
            max_score=max_score,
            compliance_level=review_result.compliance_level,
            applies=((requirement_kind or "normal") == "scoring_item" or bool(is_key_review)),
        ),
        "response_content": response_content,
        "response_completeness": response_completeness,
        "response_missing_elements": parse_json_list(response_missing_elements),
        "response_linked_to": response_linked_to,
        "response_evidence_anchor": _sx(response_evidence_anchor),
        "consistency_flag": review_result.consistency_flag,
        "evidence_relation": review_result.evidence_relation,
        "reconcile_note": _sx(review_result.reconcile_note) or None,
        # Fix-06 / 问题 3：多路检索命中（可为空列表，表示历史数据或无命中）
        "retrieval_chunks": retrieval_chunks or [],
        # Wave2-S5 P1 B1+B2+B3: 评分项规则字段（来自 scoring_metadata）+ 逐规则判定
        "score_item_name": (scoring_metadata or {}).get("score_item_name"),
        "score_rules": (scoring_metadata or {}).get("score_rules") or [],
        "deduction_rules": (scoring_metadata or {}).get("deduction_rules") or [],
        "rule_assessments": review_result.rule_assessments,
    }
    if include_debug:
        item.update(
            {
                "canonical_key": review_result.canonical_key,
                "fact_key": review_result.fact_key,
                "family_type": review_result.family_type,
                "attribution_type": review_result.attribution_type,
            }
        )
    return item


class ReviewService:
    """审查流程业务服务"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

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
            self._db, project_id, user, require_owner=require_owner
        )

    @staticmethod
    def _step_status(
        is_done: bool, is_current_stage: bool, is_failed: bool
    ) -> str:
        """推导单步状态"""
        if is_failed:
            return "failed"
        if is_done:
            return "completed"
        if is_current_stage:
            return "running"
        return "waiting"

    # ------------------------------------------------------------------
    # 1. 启动审查
    # ------------------------------------------------------------------

    async def start_review(self, project_id: int, user: User) -> dict:
        """启动AI审查流程

        前置条件:
        1. 项目有招标文档和投标文档
        2. 至少有一条已确认的招标要求
        """
        project = await self._get_project_or_raise(project_id, user)

        if project.status == ProjectStatus.REVIEWING:
            return {
                "project_id": project_id,
                "requirements_count": 0,
                "status": "processing",
                "already_started": True,
                "queue_mode": "active",
                "extraction_warnings": self._check_extraction_quality(project),
            }
        if project.status == ProjectStatus.COMPLETED:
            return {
                "project_id": project_id,
                "requirements_count": 0,
                "status": "completed",
                "already_completed": True,
                "queue_mode": "completed",
                "extraction_warnings": self._check_extraction_quality(project),
            }
        if project.status == ProjectStatus.FAILED:
            raise ServiceError("项目处于失败状态，请从审查进度页的失败恢复入口重试")

        tender_result = await self._db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
            )
        )
        tender_doc = tender_result.scalar_one_or_none()
        if not tender_doc:
            raise ServiceError("项目缺少招标文档")

        # 投标信封口径: 兼容整本单文档与分卷多文档, 禁止 scalar_one 单行假设
        from app.core.bid_envelope import load_bid_envelope

        bid_envelope = await load_bid_envelope(self._db, project_id)
        if not bid_envelope.has_documents:
            raise ServiceError("项目缺少投标文档")
        if not bid_envelope.is_complete:
            raise ServiceError(
                "投标文件未齐套（分卷模式需商务标与技术标各一份），请返回上传页补齐"
            )

        # 可评审性闸门: 仅消费"已确认且可评审"的要求（评分项恒可评审）
        req_result = await self._db.execute(
            select(Requirement).where(
                Requirement.document_id == tender_doc.id,
                *reviewable_requirement_conditions(),
            )
        )
        confirmed_requirements = req_result.scalars().all()
        if not confirmed_requirements:
            raise ServiceError(
                "主审查列表为空（没有已确认的可审查要求），"
                "请先确认招标要求；如条款均在参考条款折叠区，可将需要审查的条款升为审查项"
            )

        # 提取质量门控: 当存在严重提取缺口时提示用户
        # Fix-10 / 问题 10: 返回结构化警告透传到响应，前端据此决定是否二次确认
        extraction_quality = self._check_extraction_quality(project)

        from app.billing.services.balance_gate_error import raise_insufficient_balance
        from app.billing.services.billing_guard import BillingGuard
        from app.billing.services.wallet_service import InsufficientBalanceError
        try:
            await BillingGuard.pre_check(self._db, project, project.owner_id)
        except InsufficientBalanceError as e:
            raise_insufficient_balance(e)

        project.status = ProjectStatus.REVIEWING
        await self._db.commit()

        task_name = f"review_project_{project_id}"
        from app.core.tasks import dispatch_background_job

        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="review_project_job",
            fallback_factory=lambda: run_review_task(project_id),
            task_type="review",
            project_id=project_id,
            arq_args=(project_id,),
        )

        logger.info(
            "启动AI审查: project_id={}, requirements_count={}, queue_mode={}, arq_job_id={}",
            project_id,
            len(confirmed_requirements),
            queue_mode,
            arq_job_id,
        )
        await audit_log(
            self._db, user_id=user.id, username=user.username,
            category="bid_review",
            action="start_review", resource_type="review", resource_id=project_id,
            detail=f"启动审查, {len(confirmed_requirements)} 条已确认要求 (queue_mode={queue_mode})",
        )

        return {
            "project_id": project_id,
            "requirements_count": len(confirmed_requirements),
            "status": "processing",
            "queue_mode": queue_mode,
            # Fix-10: 透传提取质量告警，前端据此决定二次确认弹窗
            "extraction_warnings": extraction_quality,
        }

    @staticmethod
    def _check_extraction_quality(project: Project) -> dict[str, Any]:
        """提取质量门控: 当失败率或连续失败页簇超阈值时警告用户。

        不阻断流程（用户确认要求后已有人工介入），但在警告信息中
        明确标注风险区域，帮助用户判断是否需要先复核再审查。

        Fix-10: 返回结构化结果供 API 透传到前端。

        Returns:
            dict: ``{"has_issue": bool, "reasons": list[str], "raw_warnings": list}``
            - ``has_issue=True`` 时前端应弹窗二次确认
            - ``reasons`` 是已从 raw_warnings 中识别的具体风险描述
            - ``raw_warnings`` 是原始 project.extraction_warnings 列表（调试用）
        """
        warnings = project.extraction_warnings
        result: dict[str, Any] = {
            "has_issue": False,
            "reasons": [],
            "raw_warnings": [],
        }
        if not warnings or not isinstance(warnings, list):
            return result

        result["raw_warnings"] = [str(w) for w in warnings]
        reasons = [str(w) for w in warnings if "提取质量异常" in str(w)]
        result["reasons"] = reasons
        result["has_issue"] = bool(reasons)

        if result["has_issue"]:
            logger.warning(
                "启动审查前检测到提取质量异常: project_id={}, warnings={}",
                project.id,
                warnings,
            )
        return result

    # ------------------------------------------------------------------
    # 2. 获取审查进度
    # ------------------------------------------------------------------

    async def get_review_status(self, project_id: int, user: User) -> ReviewStatus:
        """获取审查进度状态（含恢复逻辑和停滞检测）。

        实现层将状态查询/推断/自愈/响应组装拆为多个 ``_`` 私有方法,
        本方法仅承担线性编排, 不变更对外契约。
        """
        project = await self._get_project_or_raise(project_id, user)
        counts = await self._load_review_progress_counts(project_id)
        task_name = f"review_project_{project_id}"

        view = self._derive_initial_stage_view(project, counts, task_name)

        task_liveness = inspect_task_liveness(
            task_name,
            heartbeat_timeout=int(getattr(settings, "ARQ_JOB_HEARTBEAT_TIMEOUT", HEARTBEAT_TIMEOUT)),
            arq_function="review_project_job",
        )
        task_active = task_liveness.is_live
        in_enqueue_grace = task_liveness.state == "enqueue_grace"

        # ── 状态自愈链: 收敛 -> 失活转失败 -> 任务丢失恢复 -> 进度停滞重启 ──
        await self._converge_completed_if_results_ready(
            project, view, counts, task_active, in_enqueue_grace, task_name,
        )
        await self._transition_failed_on_dead_task(
            project, view, counts, task_liveness, in_enqueue_grace, task_name,
        )
        await self._auto_recover_lost_task(
            project, view, task_active, in_enqueue_grace, task_name, project_id,
        )
        await self._detect_and_handle_stall(
            project, view, task_active, task_name, project_id,
        )
        self._apply_recovery_in_init_hint(view, task_name)

        # worker 仍活跃且结果已满时，保持 REVIEWING（含 retry/一致性收敛），
        # 避免四步 is_done 全绿而 overall 仍 running 的假完成态。
        if (
            project.status == ProjectStatus.REVIEWING
            and task_active
            and counts.total_requirements > 0
            and counts.reviewed_items >= counts.total_requirements
            and view.stage in (REVIEW_STAGE_RESULT_READY, REVIEW_STAGE_REVIEWING)
        ):
            heartbeat = get_task_heartbeat(task_name) or {}
            phase = str(heartbeat.get("phase") or "")
            hb_stage = str(heartbeat.get("stage") or "")
            view.stage = REVIEW_STAGE_REVIEWING
            view.review_status = "processing"
            if heartbeat_phase_belongs_to_stage(phase, hb_stage):
                view.message = (
                    "失败项重试中" if phase == "self_healing" else "AI审查收尾中"
                )
            else:
                # 主批次刚满、重试心跳尚未打上：仍属审查进行中
                view.message = "AI审查收尾中"

        steps_view = self._build_review_steps_view(view, counts, task_name)
        retry_payload = await self._load_retry_state_safe(project)
        self._reconcile_retry_running_state(view, steps_view, retry_payload)

        return self._compose_review_status_response(
            project_id, view, steps_view, counts, retry_payload,
        )

    # ------------------------------------------------------------------
    # get_review_status 内部步骤 (重构 2026-05): "搬家不改语义"。
    # 单步副作用边界已在每个 docstring 中标注。
    # ------------------------------------------------------------------

    async def _load_review_progress_counts(self, project_id: int) -> _ReviewCounts:
        """查询招标要求总数 + 已提取响应数 + 已审查数; 含上限钳制 (无 DB 写)."""
        tender_result = await self._db.execute(
            select(Document).where(
                Document.project_id == project_id,
                Document.doc_type == DocumentType.TENDER,
            )
        )
        tender_doc = tender_result.scalar_one_or_none()

        total_requirements = 0
        requirement_ids: list[int] = []
        if tender_doc:
            # 进度分母与审查消费口径一致（已确认且可评审）
            req_result = await self._db.execute(
                select(Requirement).where(
                    Requirement.document_id == tender_doc.id,
                    *reviewable_requirement_conditions(),
                )
            )
            requirements = req_result.scalars().all()
            total_requirements = len(requirements)
            requirement_ids = [r.id for r in requirements]

        extracted_responses = 0
        if requirement_ids:
            response_result = await self._db.execute(
                select(func.count(func.distinct(BidResponse.requirement_id))).where(
                    BidResponse.requirement_id.in_(requirement_ids)
                )
            )
            extracted_responses = response_result.scalar() or 0

        result_count = await self._db.execute(
            select(func.count(func.distinct(ReviewResult.requirement_id))).where(
                ReviewResult.project_id == project_id
            )
        )
        reviewed_items = result_count.scalar() or 0

        if total_requirements > 0:
            extracted_responses = min(extracted_responses, total_requirements)
            reviewed_items = min(reviewed_items, total_requirements)

        return _ReviewCounts(
            total_requirements=total_requirements,
            extracted_responses=extracted_responses,
            reviewed_items=reviewed_items,
        )

    def _derive_initial_stage_view(
        self,
        project: Project,
        counts: _ReviewCounts,
        task_name: str,
    ) -> _ReviewStageView:
        """从 Project.status + counts 推导初始 stage/status/message/progress.

        副作用: 仅 COMPLETED 分支会调用 ``reset_recovery_count(task_name)``,
        这是源代码的既有行为, 此处保留。其余分支无副作用。
        """
        total = counts.total_requirements
        extracted = counts.extracted_responses
        reviewed = counts.reviewed_items

        if total > 0:
            extraction_progress = min(extracted / total, 1.0)
            review_progress = min(reviewed / total, 1.0)
            match_progress = 1.0 if extracted >= total else 0.0
            result_progress = 1.0 if reviewed >= total > 0 else 0.0
            progress = (
                extraction_progress + match_progress + review_progress + result_progress
            ) / 4
        else:
            extraction_progress = 0.0
            review_progress = 0.0
            match_progress = 0.0
            result_progress = 0.0
            progress = 0.0

        if project.status == ProjectStatus.REVIEWING:
            review_status = "processing"
            heartbeat = get_task_heartbeat(task_name) or {}
            hb_stage = str(heartbeat.get("stage") or "")
            if total == 0:
                stage = REVIEW_STAGE_PENDING
                message = "等待启动审查"
            elif extracted < total:
                stage = REVIEW_STAGE_EXTRACTING
                message = f"投标响应提取中 {extracted}/{total}"
            elif heartbeat_text_matches_tokens(
                hb_stage, stage_tokens_for_review_step(REVIEW_STEP_LABEL_REVIEW)
            ):
                # 心跳已到步骤4：不得因 reviewed==0 把舞台锁在装配
                stage = REVIEW_STAGE_REVIEWING
                message = f"AI审查中 {reviewed}/{total}"
            elif reviewed == 0:
                stage = REVIEW_STAGE_ASSEMBLING
                message = "响应装配校验中"
            elif reviewed < total:
                stage = REVIEW_STAGE_REVIEWING
                message = f"AI审查中 {reviewed}/{total}"
            else:
                stage = REVIEW_STAGE_RESULT_READY
                message = "审查结果已就绪，可查看结果或生成报告"
        elif project.status == ProjectStatus.COMPLETED:
            review_status = "completed"
            stage = REVIEW_STAGE_COMPLETED
            message = "审查完成"
            progress = 1.0
            reset_recovery_count(task_name)
        elif project.status == ProjectStatus.FAILED:
            review_status = "failed"
            stage = REVIEW_STAGE_FAILED
            message = "审查失败"
        else:
            review_status = "pending"
            stage = REVIEW_STAGE_PENDING
            message = "等待启动审查"

        processed_items = max(extracted, reviewed)
        if total > 0:
            processed_items = min(processed_items, total)

        return _ReviewStageView(
            stage=stage,
            review_status=review_status,
            message=message,
            processed_items=processed_items,
            progress=progress,
            extraction_progress=extraction_progress,
            review_progress=review_progress,
            match_progress=match_progress,
            result_progress=result_progress,
            is_recovering=False,
            current_recovery_count=get_recovery_count(task_name),
        )

    async def _converge_completed_if_results_ready(
        self,
        project: Project,
        view: _ReviewStageView,
        counts: _ReviewCounts,
        task_active: bool,
        in_enqueue_grace: bool,
        task_name: str,
    ) -> None:
        """结果已就绪但 Project.status 未收敛时, 自动写 COMPLETED + 计费结算.

        与原始代码 ``# Fix: "结果已就绪但 Project.status 未收敛" 的兜底`` 段
        语义一致; 副作用包含 ``self._db.commit()`` 与 ``BillingGuard.on_completed``。
        """
        if not (
            project.status == ProjectStatus.REVIEWING
            and not task_active
            and not in_enqueue_grace
            and counts.total_requirements > 0
            and counts.reviewed_items >= counts.total_requirements
        ):
            return

        logger.warning(
            "审查结果已就绪但 Project.status 未收敛, 自动补写 COMPLETED: "
            "project_id={}, reviewed={}/{}",
            project.id,
            counts.reviewed_items,
            counts.total_requirements,
        )
        project.status = ProjectStatus.COMPLETED
        project.error_message = None
        await self._db.commit()
        try:
            from app.billing.services.billing_guard import BillingGuard
            await BillingGuard.on_completed(self._db, project)
            await self._db.commit()
        except Exception as billing_err:
            try:
                await self._db.rollback()
            except Exception:
                pass
            logger.warning(
                "状态收敛时计费结算异常(不影响项目状态): project_id={}, err={}",
                project.id,
                billing_err,
            )
        view.review_status = "completed"
        view.stage = REVIEW_STAGE_COMPLETED
        view.message = "审查完成"
        view.progress = 1.0
        reset_recovery_count(task_name)

    async def _transition_failed_on_dead_task(
        self,
        project: Project,
        view: _ReviewStageView,
        counts: _ReviewCounts,
        task_liveness,
        in_enqueue_grace: bool,
        task_name: str,
    ) -> None:
        """task_liveness.state in {stale, missing} 且未结果就绪 -> 转 FAILED."""
        if not (
            project.status == ProjectStatus.REVIEWING
            and task_liveness.state in {"stale", "missing"}
            and not in_enqueue_grace
            and not (
                counts.total_requirements > 0
                and counts.reviewed_items >= counts.total_requirements
            )
        ):
            return

        # 心跳 stale/missing 不等于执行体已死。ARQ 仍在途时写 FAILED
        # 会让前端弹出可点重试，显式重试再入队叠成并行 job。
        from app.core.tasks.lifecycle.liveness_evidence import is_arq_job_possibly_inflight

        if await is_arq_job_possibly_inflight(
            get_task_heartbeat(task_name),
            task_name=task_name,
            arq_function="review_project_job",
        ):
            logger.info(
                "审查失活转失败前检测到 ARQ job 仍在途，保持审查中: "
                "project_id={}, task={}, state={}",
                project.id,
                task_name,
                task_liveness.state,
            )
            return

        project.status = ProjectStatus.FAILED
        liveness_reason = str(getattr(task_liveness, "reason", "") or "")
        if task_liveness.state == "missing":
            project.error_message = (
                "审查任务异常终止，请检查 Worker 状态后重新发起审查"
            )
        elif liveness_reason.startswith("worker_missing"):
            project.error_message = (
                "审查 Worker 失联，请检查 Worker 状态后重新发起审查"
            )
        else:
            project.error_message = (
                "审查任务心跳超时，请检查 Worker 状态后重新发起审查"
            )
        await self._db.commit()
        view.review_status = "failed"
        view.stage = REVIEW_STAGE_FAILED
        view.message = project.error_message
        # 预占释放(P1/H6): 心跳失活转 FAILED 时同步释放
        try:
            from app.billing.services.billing_guard import BillingGuard
            await BillingGuard.on_failed(self._db, project)
            await self._db.commit()
        except Exception as billing_err:
            logger.warning(
                "审查心跳失活释放预占异常(不影响失败状态): project={}, err={}",
                project.id, billing_err,
            )
        logger.warning(
            "审查任务失活并转失败: project_id={}, task={}, state={}, reason={}, age={}",
            project.id,
            task_name,
            task_liveness.state,
            task_liveness.reason,
            task_liveness.age_seconds,
        )

    async def _auto_recover_lost_task(
        self,
        project: Project,
        view: _ReviewStageView,
        task_active: bool,
        in_enqueue_grace: bool,
        task_name: str,
        project_id: int,
    ) -> None:
        """任务丢失自动恢复: 含 ``_recheck_project_terminal`` 与 MAX_RECOVERY 兜底.

        副作用: ``self._db.commit()`` / ``cancel_task`` / ``release_project_binding`` /
        ``dispatch_background_job`` / ``increment_recovery_count``。
        """
        if not (
            project.status == ProjectStatus.REVIEWING
            and not task_active
            and not in_enqueue_grace
        ):
            return

        terminal_snapshot = await _recheck_project_terminal(self._db, project_id)
        if terminal_snapshot and terminal_snapshot[0] == ProjectStatus.COMPLETED:
            view.review_status = "completed"
            view.stage = REVIEW_STAGE_COMPLETED
            view.message = "审查完成"
            view.progress = 1.0
            reset_recovery_count(task_name)
            return
        if terminal_snapshot and terminal_snapshot[0] == ProjectStatus.FAILED:
            view.review_status = "failed"
            view.stage = REVIEW_STAGE_FAILED
            view.message = terminal_snapshot[1] or "处理失败"
            return

        heartbeat = get_task_heartbeat(task_name)
        if heartbeat:
            logger.warning(
                "审查任务异常（心跳超时），自动恢复: project_id={}, "
                "最后阶段={}, 进度={}",
                project_id,
                heartbeat.get("stage", "unknown"),
                heartbeat.get("progress", "unknown"),
            )
        else:
            logger.warning("审查任务缺失，自动恢复: project_id={}", project_id)

        if view.current_recovery_count >= MAX_RECOVERY:
            logger.error(
                "审查恢复次数已达上限({}), 放弃恢复: project_id={}",
                MAX_RECOVERY,
                project_id,
            )
            project.status = ProjectStatus.FAILED
            project.error_message = (
                f"任务恢复失败(已重试{MAX_RECOVERY}次)，"
                f"请检查服务器环境后重新发起审查"
            )
            await self._db.commit()
            view.review_status = "failed"
            view.stage = REVIEW_STAGE_FAILED
            view.message = project.error_message
            # 预占释放(P1/H6): 恢复超限转 FAILED 时同步释放
            try:
                from app.billing.services.billing_guard import BillingGuard
                await BillingGuard.on_failed(self._db, project)
                await self._db.commit()
            except Exception as billing_err:
                logger.warning(
                    "审查恢复超限释放预占异常(不影响失败状态): project={}, err={}",
                    project.id, billing_err,
                )
            return

        # cancel_task 只取消本进程 asyncio 任务，对 ARQ worker 进程内正在
        # 执行的 job 无效。旧 job 仍在途时重投会让两个 job 并发写同一项目，撞上
        # uq_review_project_requirement 唯一约束（历史前科见 mark_task_queued）。
        # 放在 increment_recovery_count 之前，避免在途等待白白消耗恢复配额。
        from app.core.tasks.lifecycle.liveness_evidence import is_arq_job_possibly_inflight

        if await is_arq_job_possibly_inflight(
            heartbeat,
            task_name=task_name,
            arq_function="review_project_job",
        ):
            logger.info(
                "审查恢复前检测到 ARQ job 仍在途，跳过本轮重投: project_id={}, task={}",
                project_id,
                task_name,
            )
            _base_message = view.message or "审查进行中"
            view.message = f"{_base_message}（上一次任务仍在退出中，稍后自动重试）"
            return

        # hold 可能已被 sweeper 超24h强制释放，自动恢复前必须
        # 确保预占存在（有 HOLDING 则幂等复用），否则无持仓续跑完成后
        # on_completed 幂等跳过、零结算。余额不足截断自动推进并提示充值，不置败。
        from app.billing.services.billing_guard import BillingGuard
        from app.billing.services.wallet_service import (
            InsufficientBalanceError,
            format_insufficient_balance,
        )
        balance_blocked = False
        try:
            await BillingGuard.pre_check(self._db, project, project.owner_id)
            await self._db.commit()
        except InsufficientBalanceError as balance_err:
            balance_blocked = True
            view.is_recovering = True
            view.message = (
                "余额不足，自动恢复已暂停："
                f"{format_insufficient_balance(balance_err)}，"
                "请充值后在结果页重试"
            )
            logger.warning(
                "审查自动恢复因余额不足暂停: project_id={}, {}",
                project_id,
                balance_err,
            )

        if balance_blocked:
            return

        new_count = increment_recovery_count(task_name)
        view.current_recovery_count = new_count
        view.is_recovering = True
        cancel_task(task_name)
        from app.core.ai import release_project_binding
        from app.core.tasks import dispatch_background_job
        await release_project_binding(project_id)
        await dispatch_background_job(
            task_name=task_name,
            arq_function="review_project_job",
            fallback_factory=lambda: run_review_task(project_id),
            task_type="review",
            project_id=project_id,
            arq_args=(project_id,),
        )
        view.message = (
            f"{view.message}（检测到处理异常，正在自动恢复"
            f"（第{new_count}/{MAX_RECOVERY}次），已完成的部分将被跳过）"
        )

    async def _detect_and_handle_stall(
        self,
        project: Project,
        view: _ReviewStageView,
        task_active: bool,
        task_name: str,
        project_id: int,
    ) -> None:
        """进度停滞检测: DB 计数未变 + 心跳过期 -> 重启任务.

        副作用: 写入 ``_progress_cache`` / ``self._db.commit()`` /
        ``cancel_task`` / ``release_project_binding`` / ``dispatch_background_job``。
        """
        if not (project.status == ProjectStatus.REVIEWING and task_active):
            return

        now = time.time()
        cached = _progress_cache.get(project_id)
        processed_items = view.processed_items
        if not cached or processed_items > cached["processed"]:
            _progress_cache[project_id] = {
                "processed": float(processed_items),
                "updated_at": now,
            }
            return

        if now - cached["updated_at"] <= settings.REVIEW_STALL_TIMEOUT:
            return

        heartbeat = get_task_heartbeat(task_name)
        heartbeat_ts = heartbeat.get("last_heartbeat", 0) if heartbeat else 0
        heartbeat_fresh = (
            (now - heartbeat_ts) < HEARTBEAT_TIMEOUT if heartbeat_ts else False
        )

        if heartbeat_fresh:
            logger.debug(
                "DB进度未更新但心跳仍活跃, 跳过重启: project_id={}, "
                "心跳阶段={}",
                project_id,
                heartbeat.get("stage", "unknown"),
            )
            return

        terminal_snapshot = await _recheck_project_terminal(self._db, project_id)
        if terminal_snapshot and terminal_snapshot[0] == ProjectStatus.COMPLETED:
            view.review_status = "completed"
            view.stage = REVIEW_STAGE_COMPLETED
            view.message = "审查完成"
            view.progress = 1.0
            reset_recovery_count(task_name)
            return
        if terminal_snapshot and terminal_snapshot[0] == ProjectStatus.FAILED:
            view.review_status = "failed"
            view.stage = REVIEW_STAGE_FAILED
            view.message = terminal_snapshot[1] or "处理失败"
            return

        if view.current_recovery_count >= MAX_RECOVERY:
            logger.error(
                "审查停滞恢复次数已达上限({}): project_id={}",
                MAX_RECOVERY,
                project_id,
            )
            project.status = ProjectStatus.FAILED
            project.error_message = (
                f"任务恢复失败(已重试{MAX_RECOVERY}次)，"
                f"请检查服务器环境后重新发起审查"
            )
            await self._db.commit()
            view.review_status = "failed"
            view.stage = REVIEW_STAGE_FAILED
            view.message = project.error_message
            # 预占释放(P1/H6): 恢复超限转 FAILED 时同步释放
            try:
                from app.billing.services.billing_guard import BillingGuard
                await BillingGuard.on_failed(self._db, project)
                await self._db.commit()
            except Exception as billing_err:
                logger.warning(
                    "审查恢复超限释放预占异常(不影响失败状态): project={}, err={}",
                    project.id, billing_err,
                )
            return

        # 停滞重启与丢失恢复共用同一预检，禁止无持仓续跑。
        from app.billing.services.billing_guard import BillingGuard
        from app.billing.services.wallet_service import (
            InsufficientBalanceError,
            format_insufficient_balance,
        )
        balance_blocked = False
        try:
            await BillingGuard.pre_check(self._db, project, project.owner_id)
            await self._db.commit()
        except InsufficientBalanceError as balance_err:
            balance_blocked = True
            view.is_recovering = True
            view.message = (
                "余额不足，自动恢复已暂停："
                f"{format_insufficient_balance(balance_err)}，"
                "请充值后在结果页重试"
            )
            logger.warning(
                "审查停滞恢复因余额不足暂停: project_id={}, {}",
                project_id,
                balance_err,
            )

        if balance_blocked:
            return

        new_count = increment_recovery_count(task_name)
        view.current_recovery_count = new_count
        view.is_recovering = True
        logger.warning(
            "审查进度停滞且心跳超时, 重启任务: project_id={}, "
            "停滞时间={:.0f}s, 恢复次数={}/{}",
            project_id,
            now - cached["updated_at"],
            new_count,
            MAX_RECOVERY,
        )
        cancel_task(task_name)
        from app.core.ai import release_project_binding
        from app.core.tasks import dispatch_background_job
        await release_project_binding(project_id)
        await dispatch_background_job(
            task_name=task_name,
            arq_function="review_project_job",
            fallback_factory=lambda: run_review_task(project_id),
            task_type="review",
            project_id=project_id,
            arq_args=(project_id,),
        )
        _progress_cache[project_id] = {
            "processed": float(processed_items),
            "updated_at": now,
        }
        view.message = (
            f"{view.message}（检测到进度停滞，正在自动恢复"
            f"（第{new_count}/{MAX_RECOVERY}次），已完成的部分将被跳过）"
        )

    def _apply_recovery_in_init_hint(
        self,
        view: _ReviewStageView,
        task_name: str,
    ) -> None:
        """非本次触发但任务正处于'启动'阶段 -> 文案补"任务恢复中"提示."""
        if not (
            not view.is_recovering
            and view.current_recovery_count > 0
            and view.review_status == "processing"
        ):
            return
        heartbeat = get_task_heartbeat(task_name)
        if heartbeat and heartbeat.get("stage") == "启动":
            view.is_recovering = True
            if "自动恢复" not in (view.message or ""):
                view.message = f"{view.message}（任务恢复中，正在跳过已完成的步骤...）"

    def _build_review_steps_view(
        self,
        view: _ReviewStageView,
        counts: _ReviewCounts,
        task_name: str,
    ) -> _ReviewStepsView:
        """构造 4 个 ReviewStepStatus + overall_progress + overall_status; 仅 redis 读."""
        total = counts.total_requirements
        extracted = counts.extracted_responses
        reviewed = counts.reviewed_items
        stage = view.stage
        review_status = view.review_status

        is_failed = stage == REVIEW_STAGE_FAILED
        result_ready = reviewed >= total > 0
        report_ready = result_ready
        assemble_progress_pct = 0
        assemble_detail: Optional[str] = None

        if stage == REVIEW_STAGE_ASSEMBLING and review_status == "processing":
            heartbeat = get_task_heartbeat(task_name)
            assemble_tokens = stage_tokens_for_review_step(REVIEW_STEP_LABEL_ASSEMBLE)
            if heartbeat and heartbeat_belongs_to_running_step(heartbeat, assemble_tokens):
                raw_progress = str(heartbeat.get("progress", "") or "")
                if "/" in raw_progress:
                    try:
                        done_str, total_str = raw_progress.split("/", 1)
                        matched_done = int(done_str)
                        matched_total = int(total_str)
                        if matched_total > 0:
                            assemble_progress_pct = int(matched_done / matched_total * 100)
                            assemble_detail = f"{matched_done}/{matched_total}"
                    except ValueError:
                        assemble_progress_pct = 0
                if not assemble_detail:
                    assemble_detail = (
                        heartbeat.get("message")
                        or "装配中"
                    )
            elif heartbeat:
                assemble_detail = "装配中"

        # AI对比审查步骤进度（单调、无回跳）
        # - 主批次: reviewed/total 映射到 0-90
        # - 失败项重试: 在 90 基础上继续到 99（与主批次终点衔接）
        # - 收尾收敛: 99；RESULT_READY/COMPLETED: 100
        review_progress_pct = 0
        if stage in (REVIEW_STAGE_RESULT_READY, REVIEW_STAGE_COMPLETED):
            review_progress_pct = 100
        elif total and stage == REVIEW_STAGE_REVIEWING:
            heartbeat_for_review = get_task_heartbeat(task_name) or {}
            phase = str(heartbeat_for_review.get("phase") or "")
            hb_stage = str(heartbeat_for_review.get("stage") or "")
            if phase == "self_healing" and heartbeat_phase_belongs_to_stage(
                phase, hb_stage
            ):
                raw_retry = str(heartbeat_for_review.get("progress") or "")
                retry_done, retry_total = 0, 0
                if "/" in raw_retry:
                    try:
                        done_str, total_str = raw_retry.split("/", 1)
                        retry_done = int(done_str)
                        retry_total = int(total_str)
                    except ValueError:
                        retry_done, retry_total = 0, 0
                if retry_total > 0:
                    review_progress_pct = 90 + int(9 * retry_done / retry_total)
                else:
                    review_progress_pct = 90
                review_progress_pct = min(99, max(90, review_progress_pct))
            elif phase == "finalizing" and heartbeat_phase_belongs_to_stage(
                phase, hb_stage
            ):
                review_progress_pct = 99
            elif reviewed >= total:
                # 主批次刚满但尚未进入重试/收尾心跳时保持 90，与重试起点衔接
                review_progress_pct = 90
            else:
                # 主批次进行中：预留 90-99 给重试/收尾，避免满格后再回跳
                review_progress_pct = min(90, int(reviewed * 90 / total))
        elif total:
            review_progress_pct = round(view.review_progress * 100)

        # 与原始实现等价: 原代码用 ``project.status == ProjectStatus.COMPLETED``,
        # 重构后在 view 中通过 ``stage == REVIEW_STAGE_COMPLETED`` 表达同一含义,
        # 因为 _derive_initial_stage_view 与 _converge_completed_if_results_ready
        # 都会在 project COMPLETED 时把 view.stage 同步为 COMPLETED。
        if report_ready and stage == REVIEW_STAGE_COMPLETED:
            steps = [
                ReviewStepStatus(label=REVIEW_STEP_LABEL_EXTRACT, status="completed", progress=100, detail=None),
                ReviewStepStatus(label=REVIEW_STEP_LABEL_ASSEMBLE, status="completed", progress=100, detail=None),
                ReviewStepStatus(label=REVIEW_STEP_LABEL_REVIEW, status="completed", progress=100, detail=None),
                ReviewStepStatus(label=REVIEW_STEP_LABEL_READY, status="completed", progress=100, detail=None),
            ]
        else:
            steps = [
                ReviewStepStatus(
                    label=REVIEW_STEP_LABEL_EXTRACT,
                    status=self._step_status(
                        is_done=extracted >= total > 0,
                        is_current_stage=stage == REVIEW_STAGE_EXTRACTING,
                        is_failed=is_failed and stage == REVIEW_STAGE_EXTRACTING,
                    ),
                    progress=round(view.extraction_progress * 100) if total else 0,
                    detail=(
                        f"{extracted}/{total}"
                        if total and stage == REVIEW_STAGE_EXTRACTING
                        else None
                    ),
                ),
                ReviewStepStatus(
                    label=REVIEW_STEP_LABEL_ASSEMBLE,
                    status=self._step_status(
                        is_done=stage in (
                            REVIEW_STAGE_REVIEWING,
                            REVIEW_STAGE_RESULT_READY,
                            REVIEW_STAGE_COMPLETED,
                        ),
                        is_current_stage=stage == REVIEW_STAGE_ASSEMBLING,
                        is_failed=is_failed and stage == REVIEW_STAGE_ASSEMBLING,
                    ),
                    progress=(
                        100
                        if stage in (
                            REVIEW_STAGE_REVIEWING,
                            REVIEW_STAGE_RESULT_READY,
                            REVIEW_STAGE_COMPLETED,
                        )
                        else (assemble_progress_pct if stage == REVIEW_STAGE_ASSEMBLING else 0)
                    ),
                    detail=assemble_detail if stage == REVIEW_STAGE_ASSEMBLING else None,
                ),
                ReviewStepStatus(
                    label=REVIEW_STEP_LABEL_REVIEW,
                    status=self._step_status(
                        is_done=(
                            reviewed >= total > 0
                            and stage in (
                                REVIEW_STAGE_RESULT_READY,
                                REVIEW_STAGE_COMPLETED,
                            )
                        ),
                        is_current_stage=stage == REVIEW_STAGE_REVIEWING,
                        is_failed=is_failed and stage == REVIEW_STAGE_REVIEWING,
                    ),
                    progress=review_progress_pct,
                    detail=(
                        f"{reviewed}/{total}"
                        if total and stage == REVIEW_STAGE_REVIEWING
                        else None
                    ),
                ),
                ReviewStepStatus(
                    label=REVIEW_STEP_LABEL_READY,
                    status=self._step_status(
                        is_done=(
                            result_ready
                            and stage in (
                                REVIEW_STAGE_RESULT_READY,
                                REVIEW_STAGE_COMPLETED,
                            )
                        ),
                        is_current_stage=stage == REVIEW_STAGE_RESULT_READY,
                        is_failed=is_failed and stage == REVIEW_STAGE_RESULT_READY,
                    ),
                    progress=(
                        100
                        if (
                            result_ready
                            and stage in (
                                REVIEW_STAGE_RESULT_READY,
                                REVIEW_STAGE_COMPLETED,
                            )
                        )
                        else 0
                    ),
                    detail="结果可用" if stage == REVIEW_STAGE_RESULT_READY else None,
                ),
            ]

        if steps:
            overall_progress_pct = round(
                sum(step.progress for step in steps) / len(steps)
            )
        else:
            overall_progress_pct = round(view.progress * 100)

        if review_status == "processing":
            overall_status_str = "running"
        elif review_status == "completed":
            overall_status_str = "completed"
            overall_progress_pct = 100
        elif review_status == "failed":
            overall_status_str = "failed"
        else:
            overall_status_str = "idle"

        if overall_status_str == "running":
            heartbeat = get_task_heartbeat(task_name)
            if heartbeat:
                hb_progress = heartbeat.get("progress", "")
                phase = str(heartbeat.get("phase") or "")
                hb_stage = str(heartbeat.get("stage") or "")
                for step in steps:
                    if step.status == "running":
                        if hb_progress:
                            if (
                                phase == "self_healing"
                                and heartbeat_phase_belongs_to_stage(phase, hb_stage)
                            ):
                                step.detail = f"失败项重试 {hb_progress}"
                            elif step.label == REVIEW_STEP_LABEL_EXTRACT:
                                step.detail = merge_running_step_detail(
                                    extracted, total, heartbeat,
                                    stage_tokens=stage_tokens_for_review_step(step.label),
                                )
                            elif step.label == REVIEW_STEP_LABEL_REVIEW:
                                step.detail = merge_running_step_detail(
                                    reviewed, total, heartbeat,
                                    stage_tokens=stage_tokens_for_review_step(step.label),
                                )
                            elif step.label == REVIEW_STEP_LABEL_ASSEMBLE:
                                merged = merge_running_step_detail(
                                    0, 0, heartbeat,
                                    stage_tokens=stage_tokens_for_review_step(step.label),
                                )
                                if merged:
                                    step.detail = merged
                            else:
                                step.detail = hb_progress
                        step.progress = running_step_progress_from_heartbeat(
                            step.progress,
                            heartbeat,
                            stage_tokens=stage_tokens_for_review_step(step.label),
                        )
                        break
                overall_progress_pct = round(
                    sum(step.progress for step in steps) / len(steps)
                ) if steps else overall_progress_pct

        return _ReviewStepsView(
            steps=steps,
            overall_progress_pct=overall_progress_pct,
            overall_status_str=overall_status_str,
            result_ready=result_ready,
            report_ready=report_ready,
            assemble_progress_pct=assemble_progress_pct,
            assemble_detail=assemble_detail,
        )

    async def _load_retry_state_safe(self, project: Project) -> dict:
        """安全调用 ``build_review_retry_state``, 异常时降级为空 retry payload."""
        try:
            from app.services.flow_retry_service import build_review_retry_state

            return await build_review_retry_state(self._db, project)
        except Exception as retry_err:
            logger.debug(f"审查 retry-state 合并失败(降级为空): {retry_err}")
            return {
                "retryable": False,
                "failed_node": None,
                "target_id": None,
                "failed_stage": None,
                "retry_action_label": "",
                "preserved_outputs": [],
                "reset_scope": [],
                "last_attempt": 0,
                "retry_running": False,
                "last_error": None,
            }

    @staticmethod
    def _reconcile_retry_running_state(
        view: _ReviewStageView,
        steps_view: _ReviewStepsView,
        retry_payload: dict,
    ) -> None:
        """retry_running=True 且整体 idle 时, 把 view/steps_view 拉回 running."""
        if retry_payload.get("retry_running") and steps_view.overall_status_str == "idle":
            view.review_status = "processing"
            steps_view.overall_status_str = "running"
            view.message = retry_payload.get("retry_action_label") or "显式重试运行中"

    def _compose_review_status_response(
        self,
        project_id: int,
        view: _ReviewStageView,
        steps_view: _ReviewStepsView,
        counts: _ReviewCounts,
        retry_payload: dict,
    ) -> ReviewStatus:
        """组装最终 ReviewStatus 响应; 纯函数."""
        return ReviewStatus(
            project_id=project_id,
            status=view.review_status,
            overall_status=steps_view.overall_status_str,
            overall_progress=steps_view.overall_progress_pct,
            steps=steps_view.steps,
            error_message=view.message if view.review_status == "failed" else None,
            is_recovering=view.is_recovering,
            recovery_count=view.current_recovery_count,
            retryable=bool(retry_payload.get("retryable")),
            failed_node=retry_payload.get("failed_node"),
            target_id=retry_payload.get("target_id"),
            failed_stage=retry_payload.get("failed_stage"),
            retry_action_label=str(retry_payload.get("retry_action_label") or ""),
            preserved_outputs=list(retry_payload.get("preserved_outputs") or []),
            reset_scope=list(retry_payload.get("reset_scope") or []),
            last_attempt=int(retry_payload.get("last_attempt") or 0),
            retry_running=bool(retry_payload.get("retry_running")),
            last_error=retry_payload.get("last_error"),
            stage=view.stage,
            total_items=counts.total_requirements,
            processed_items=view.processed_items,
            progress=view.progress,
            message=view.message,
            extraction_total=counts.total_requirements,
            extraction_done=counts.extracted_responses,
            review_done=counts.reviewed_items,
            report_ready=steps_view.report_ready,
            result_ready=steps_view.result_ready,
            report_available=steps_view.result_ready,
        )

    # ------------------------------------------------------------------
    # 3. 获取审查结果
    # ------------------------------------------------------------------

    async def get_review_results(
        self, project_id: int, user: User, include_debug: bool = False
    ) -> dict:
        """获取审查结果列表及汇总统计"""
        project = await self._get_project_or_raise(project_id, user)
        if include_debug and not user.is_admin:
            raise ServiceError("调试视图需要管理员权限", status_code=403)

        result = await self._db.execute(
            select(
                ReviewResult,
                Requirement.category,
                Requirement.description,
                Requirement.source_page,
                Requirement.source_pages,
                Requirement.keywords,
                Requirement.sub_requirements,
                Requirement.merged_count,
                Requirement.subject_scope,
                Requirement.main_requirement_id,
                Requirement.requirement_kind,
                Requirement.score_weight,
                Requirement.is_key_review,
                Requirement.scoring_metadata,
                Requirement.review_relevance,
                BidResponse.content,
                BidResponse.source_pages,
                BidResponse.completeness,
                BidResponse.missing_elements,
                BidResponse.linked_to,
                BidResponse.evidence_anchor,
                BidResponse.document_id,
            )
            .join(Requirement, ReviewResult.requirement_id == Requirement.id)
            .outerjoin(BidResponse, ReviewResult.bid_response_id == BidResponse.id)
            .where(ReviewResult.project_id == project_id)
            .order_by(ReviewResult.id)
        )
        rows = result.all()

        # 投标信封: 文档 id -> 卷角色映射(整本全为 None, 分卷标注商务/技术标)
        from app.core.bid_envelope import load_bid_envelope
        from app.services.review_envelope_sources import attach_envelope_sources
        from app.services.review_page_excerpts import attach_page_excerpts

        part_by_document = (
            await load_bid_envelope(self._db, project_id)
        ).part_by_document()

        # Fix-06: 一次性 join 查出所有项目的 retrieval_chunks 按 bid_response_id 分组
        retrieval_chunks_map: dict[int, list[dict[str, Any]]] = {}
        try:
            from app.models.bid_response_chunk import BidResponseChunk
            from app.models.chunk import Chunk
            bid_response_ids = [
                rr.bid_response_id for (rr, *_) in rows
                if rr.bid_response_id is not None
            ]
            if bid_response_ids:
                chunks_stmt = (
                    select(
                        BidResponseChunk.bid_response_id,
                        BidResponseChunk.chunk_id,
                        BidResponseChunk.page_num,
                        BidResponseChunk.similarity,
                        BidResponseChunk.role,
                        BidResponseChunk.matched_sub_query,
                        BidResponseChunk.document_id,
                        Chunk.content,
                    )
                    .select_from(BidResponseChunk)
                    .outerjoin(Chunk, Chunk.id == BidResponseChunk.chunk_id)
                    .where(BidResponseChunk.bid_response_id.in_(bid_response_ids))
                    .order_by(BidResponseChunk.id)
                )
                chunk_rows = (await self._db.execute(chunks_stmt)).all()
                from app.services.review_page_excerpts import clip_excerpt
                for br_id, chunk_id, page_num, similarity, role, matched_sq, doc_id, content in chunk_rows:
                    retrieval_chunks_map.setdefault(br_id, []).append({
                        "chunk_id": chunk_id,
                        "page_num": page_num,
                        "similarity": float(similarity) if similarity is not None else None,
                        "role": role,
                        "matched_sub_query": matched_sq,
                        "document_id": doc_id,
                        "content_preview": clip_excerpt(content),
                    })
        except Exception as err:
            # Fix-06: 新表读取失败时 retrieval_chunks 默认空列表（不阻断 API）
            logger.debug(
                f"get_review_results: retrieval_chunks join 降级, "
                f"project_id={project_id}, err={err}"
            )

        results: list[Any] = []
        result_dicts: list[dict] = []
        high_risk_items: list[dict] = []
        summary_items: list[ReviewSummaryItem] = []

        for (
            review_result,
            category,
            requirement_description,
            requirement_source_page,
            requirement_source_pages,
            requirement_keywords,
            requirement_sub_requirements,
            requirement_merged_count,
            subject_scope,
            main_requirement_id,
            requirement_kind,
            max_score,
            is_key_review,
            scoring_metadata,
            review_relevance,
            response_content,
            source_pages,
            response_completeness,
            response_missing_elements,
            response_linked_to,
            response_evidence_anchor,
            response_document_id,
        ) in rows:
            review_result.category = category
            review_result.subject_scope = subject_scope
            review_result.main_requirement_id = (
                review_result.main_requirement_id or main_requirement_id
            )
            review_result.source_pages = source_pages
            results.append(review_result)
            # P2.2: 参考条款不计入汇总评分（明细保留展示+不计分标识）;
            # 人工降级后按当前 review_relevance 即时排除, 无需重审。评分项恒计入。
            if counts_toward_review_summary(
                review_relevance=review_relevance,
                requirement_kind=requirement_kind,
                is_key_review=is_key_review,
            ):
                summary_items.append(
                    ReviewSummaryItem(
                        category=category.value if hasattr(category, "value") else str(category),
                        is_compliant=bool(review_result.is_compliant),
                        compliance_level=review_result.compliance_level,
                        risk_level=review_result.risk_level,
                        score=review_result.score,
                    )
                )
            item = _build_review_result_item(
                review_result,
                category=category.value if hasattr(category, "value") else str(category),
                subject_scope=subject_scope,
                main_requirement_id=main_requirement_id,
                source_pages=source_pages,
                requirement_description=requirement_description,
                requirement_source_page=requirement_source_page,
                requirement_source_pages=requirement_source_pages,
                requirement_keywords=requirement_keywords,
                requirement_sub_requirements=requirement_sub_requirements,
                requirement_merged_count=requirement_merged_count,
                requirement_kind=requirement_kind,
                max_score=max_score,
                is_key_review=is_key_review,
                scoring_metadata=scoring_metadata,
                review_relevance=(
                    review_relevance.value
                    if hasattr(review_relevance, "value")
                    else review_relevance
                ),
                response_content=response_content,
                response_completeness=response_completeness,
                response_missing_elements=response_missing_elements,
                response_linked_to=response_linked_to,
                response_evidence_anchor=response_evidence_anchor,
                retrieval_chunks=(
                    retrieval_chunks_map.get(review_result.bid_response_id)
                    if review_result.bid_response_id is not None
                    else None
                ),
                include_debug=include_debug,
            )
            # 投标信封来源: 按命中文档标注卷角色与页码定位(整本产出空列表)
            attach_envelope_sources(
                item,
                source_document_id=response_document_id,
                retrieval_chunks=item.get("retrieval_chunks"),
                part_by_document=part_by_document,
            )
            attach_page_excerpts(
                item,
                retrieval_chunks=item.get("retrieval_chunks"),
                fallback_document_id=response_document_id,
            )
            result_dicts.append(item)
            if review_result.risk_level == RiskLevel.HIGH:
                high_risk_items.append(item)

        summary = build_review_summary(project_id, summary_items)
        # P2 闭环：合并评分项预估总得分（result_dicts 已含 requirement_kind/max_score/estimated_score/category）
        summary.update(compute_scoring_aggregate(result_dicts))

        warnings = project.extraction_warnings or []

        if not results:
            return {"summary": summary, "results": [], "high_risk_items": [], "extraction_warnings": warnings}

        logger.debug("获取审查结果: project_id={}, count={}", project_id, len(results))

        return {
            "summary": summary,
            "results": result_dicts,
            "high_risk_items": high_risk_items,
            "extraction_warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 4. 生成报告
    # ------------------------------------------------------------------

    async def generate_report(
        self, project_id: int, user: User, include_debug: bool = False
    ) -> tuple[bytes, str]:
        """生成PDF审查报告，返回 (pdf_bytes, filename)"""
        project = await self._get_project_or_raise(project_id, user)
        if include_debug and not user.is_admin:
            raise ServiceError("调试视图需要管理员权限", status_code=403)

        result = await self._db.execute(
            select(ReviewResult)
            .where(ReviewResult.project_id == project_id)
            .options(
                selectinload(ReviewResult.requirement),
                selectinload(ReviewResult.bid_response).selectinload(
                    BidResponse.retrieval_chunks
                ),
            )
            .order_by(ReviewResult.id)
        )
        results = result.scalars().all()

        if not results:
            raise ServiceError("项目没有审查结果，无法生成报告")

        summary = calculate_review_summary(project_id, results)

        results_data = [
            _build_review_result_item(
                r,
                category=(
                    r.requirement.category.value
                    if r.requirement and hasattr(r.requirement.category, "value")
                    else "technical"
                ),
                subject_scope=r.requirement.subject_scope if r.requirement else None,
                main_requirement_id=r.requirement.main_requirement_id if r.requirement else None,
                source_pages=r.bid_response.source_pages if r.bid_response else None,
                requirement_description=r.requirement.description if r.requirement else None,
                requirement_source_page=r.requirement.source_page if r.requirement else None,
                requirement_source_pages=r.requirement.source_pages if r.requirement else None,
                requirement_keywords=r.requirement.keywords if r.requirement else None,
                requirement_sub_requirements=r.requirement.sub_requirements if r.requirement else None,
                requirement_merged_count=r.requirement.merged_count if r.requirement else None,
                requirement_kind=(r.requirement.requirement_kind if r.requirement else None),
                max_score=(r.requirement.score_weight if r.requirement else None),
                is_key_review=(bool(r.requirement.is_key_review) if r.requirement else False),
                scoring_metadata=(r.requirement.scoring_metadata if r.requirement else None),
                review_relevance=(
                    getattr(r.requirement, "review_relevance", None) if r.requirement else None
                ),
                response_content=r.bid_response.content if r.bid_response else None,
                response_completeness=r.bid_response.completeness if r.bid_response else None,
                response_missing_elements=r.bid_response.missing_elements if r.bid_response else None,
                response_linked_to=r.bid_response.linked_to if r.bid_response else None,
                response_evidence_anchor=r.bid_response.evidence_anchor if r.bid_response else None,
                include_debug=include_debug,
            )
            for r in results
        ]

        # 投标信封来源: 报告与结果 API 同口径标注卷角色/页码定位(整本为空列表)
        from app.core.bid_envelope import load_bid_envelope
        from app.services.review_envelope_sources import attach_envelope_sources

        part_by_document = (
            await load_bid_envelope(self._db, project_id)
        ).part_by_document()
        for r, item in zip(results, results_data):
            attach_envelope_sources(
                item,
                source_document_id=(
                    r.bid_response.document_id if r.bid_response else None
                ),
                retrieval_chunks=[
                    {"page_num": c.page_num, "document_id": c.document_id}
                    for c in (
                        r.bid_response.retrieval_chunks if r.bid_response else []
                    )
                ],
                part_by_document=part_by_document,
            )

        # 与 results_data 构建参数完全一致，直接复用已构建项（生成器只读不改）
        high_risk_items = [
            item
            for r, item in zip(results, results_data)
            if r.risk_level == RiskLevel.HIGH
        ]

        # 文档质检章节载荷(独立结果域; 读取失败不阻断报告)
        from app.services.document_qa.read_service import load_report_payload

        try:
            document_qa_payload = await load_report_payload(self._db, project_id)
        except Exception as qa_err:
            logger.warning(f"文档质检报告载荷读取失败(章节显示未执行): {qa_err}")
            document_qa_payload = None

        generator = get_report_generator()
        pdf_bytes = generator.generate(
            project_name=project.name,
            summary=summary,
            results=results_data,
            high_risk_items=high_risk_items,
            include_debug=include_debug,
            document_qa=document_qa_payload,
        )

        filename = build_export_filename(project.name, "投标审查", "pdf")
        logger.info(
            "生成PDF报告: project_id={}, size={} bytes", project_id, len(pdf_bytes)
        )
        await audit_log(
            self._db, user_id=user.id, username=user.username,
            category="bid_review",
            action="download", resource_type="report", resource_id=project_id,
            detail=f"下载审查报告: {filename}",
        )
        return pdf_bytes, filename

    # ------------------------------------------------------------------
    # 5. 一致性重算
    # ------------------------------------------------------------------

    async def reconcile(self, project_id: int, user: User) -> dict:
        """对历史审查结果执行一致性重算"""
        await self._get_project_or_raise(project_id, user)

        orchestrator = get_review_orchestrator(db=self._db)
        await orchestrator._post_review_consistency_check(project_id)  # noqa: SLF001

        total = (
            await self._db.scalar(
                select(func.count(ReviewResult.id)).where(
                    ReviewResult.project_id == project_id
                )
            )
            or 0
        )
        logger.info("审查结果重算完成: project_id={}, total={}", project_id, total)
        return {"project_id": project_id, "reconciled_count": int(total)}
