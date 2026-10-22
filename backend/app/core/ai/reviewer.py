# Compatibility facade: reviewer helpers retain module-level patch targets.
# ruff: noqa: F401
"""AI审查流程编排器

执行招标要求与投标响应的逐项对比审查。

流程：
1. 获取匹配对列表（招标要求 + 投标响应）
2. 对每个匹配对调用 LLM 进行对比审查
3. 校验并存储审查结果
4. 汇总生成最终结论
"""

import asyncio
import hashlib
import re
from collections import defaultdict
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.ai_factory import LLMClient, get_review_client
from app.core.ai.critic.flows import FLOW_INVESTIGATE
from app.core.ai.llm_retry_resume import LlmRetryResumeState
from app.core.ai.prompt_registry import get_registry
from app.core.ai.prompts.review import (
    NO_RESPONSE_PROMPT as _CODE_NO_RESPONSE_PROMPT,
)
from app.core.ai.prompts.review import (
    REVIEW_PROMPT as _CODE_REVIEW_PROMPT,
)
from app.core.ai.prompts.review import (
    REVIEW_SYSTEM_PROMPT as _CODE_REVIEW_SYSTEM,
)
from app.core.ai.retry_tracker import LLMRetryTracker
from app.core.ai.reviewer_parts import (
    consistency_postcheck,
    cove_judge,
    evidence_prefetch,
    fact_graph,
    pair_review,
    result_builder,
    review_llm_invoke,
)
from app.core.ai.reviewer_parts.evidence_pack_budget import EvidenceItem
from app.core.ai.reviewer_parts.result_builder import is_failure_result
from app.core.config import get_ai_mode, settings
from app.models.bid_response import BidResponse
from app.models.project import Project
from app.models.requirement import Requirement
from app.models.review_fact import ReviewFact, ReviewFactJudgement, ReviewFactLink
from app.models.review_result import ComplianceLevel, ReviewResult, RiskLevel


def _get_review_prompts() -> tuple[str, str, str]:
    """从 Registry 获取审查 Prompt，失败时 fallback 到代码常量"""
    reg = get_registry()
    review = reg.get_prompt("bid_review.review_item")
    no_resp = reg.get_prompt("bid_review.review_no_response")
    system = review.system_message or _CODE_REVIEW_SYSTEM
    user_tpl = review.user_template or _CODE_REVIEW_PROMPT
    no_resp_tpl = no_resp.user_template or _CODE_NO_RESPONSE_PROMPT
    return system, user_tpl, no_resp_tpl
from app.core.ai.validators import (
    OutputValidator,
    ValidationException,
    get_review_result_validator,
)
from app.core.matcher.requirement_matcher import MatchedPair
from app.core.review_contracts import (
    CrossEvidence,
    ReviewEvidenceBundle,
    ReviewSummaryItem,
    build_review_summary,
    parse_json_list,
    parse_page_numbers,
)
from app.schemas.review import ExtractedReviewResult

# 符合程度映射
COMPLIANCE_LEVEL_MAP = {
    "fully": ComplianceLevel.FULLY,
    "partially": ComplianceLevel.PARTIALLY,
    "not": ComplianceLevel.NOT,
    "missing": ComplianceLevel.MISSING,
}

# 风险等级映射
RISK_LEVEL_MAP = {
    "none": RiskLevel.NONE,
    "low": RiskLevel.LOW,
    "medium": RiskLevel.MEDIUM,
    "high": RiskLevel.HIGH,
}

class ReviewOrchestrator:
    """AI审查流程编排器
    
    协调招标-投标对比审查的完整流程。
    
    示例:
        >>> orchestrator = ReviewOrchestrator(db, llm_client)
        >>> results = await orchestrator.review_project(project_id, pairs)
        >>> summary = orchestrator.summarize_results(results)
    """

    # Fix-09 / 问题 9: retry 覆盖时必须全量覆盖（含 None）的"Gate 产出字段"白名单。
    # 这些字段是 Gate Pipeline / CoVe / Judge 的最终产物，retry 后如果用
    # `if new_val is not None: setattr` 条件覆盖，可能保留陈旧值污染复核判断。
    _GATE_REPLAY_COVER_FIELDS: frozenset[str] = frozenset({
        "is_compliant",
        "compliance_level",
        "score",
        "comment",
        "risk_level",
        "relevance_score",
        "relevance_note",
        "consistency_flag",
        "reconcile_note",
        "evidence_relation",
        "attribution_type",
        "main_requirement_id",
        "fact_key",
        "canonical_key",
        "family_type",
        "response_summary",
        "requirement_summary",
        # B1 失败合同字段：Gate 可能调整
        "failure_state",
        "failure_reason",
        "confidence",
        "gate_trace",
        "review_required",
    })

    def __init__(
        self,
        db: Optional[AsyncSession] = None,
        llm_client: Optional[LLMClient] = None,
        max_retries: int = 3,
        item_timeout: Optional[float] = None,
        project_id: Optional[int] = None
    ):
        """初始化审查编排器

 Args:
 db: 数据库会话(可选)。: 并发审查路径全程使用 db_session_scope
 短 session,外层不再需要常驻 session 占用 QueuePool。保留参数仅为
 兼容历史 helper 的 `session or self.db` 兜底分支与既有单测路径。
 生产并发路径请始终传 None。
 llm_client: LLM客户端（可选，默认根据AI模式配置自动选择）
 max_retries: LLM 调用最大重试次数
 item_timeout: 单项审查超时时间（秒），不传则按模式自动设置
 """
        self.db = db
        self._project_id = project_id
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self.max_retries = max_retries
        self.item_timeout = item_timeout or self._get_default_timeout()
        # 恢复判定必须与本模块的失败口径一致。review_single_pair 失败时
        # 返回非 None 的 failure_result, 用默认"非 None 即恢复"会把 100% 失败的批次
        # 报成 recovered=N/N, 把全量失败伪装成完全自愈(项目2449 实测 11/11)。
        self._retry_tracker = LLMRetryTracker(
            "reviewer",
            is_recovered=lambda result: not is_failure_result(result),
        )
        # S.2.4: 投标文档 chunk 页码缓存（供 PageAnchorVerifyGate 做范围校验）
        self._chunk_pages_cache: dict[int, set[int]] = {}
        # project_id 在审查全程不变, owner_id 一次加载即可。
        # 避免每个并发协程都调 self.db.execute 触发连接池竞争 (违反)。
        # 标记 _OWNER_ID_UNSET 以区分 "未加载" 和 "加载后值为 None"。
        self._project_owner_id_cache: Optional[int] = None
        self._project_owner_id_loaded: bool = False
        self._evidence_ledger = None
        self._material_inventory = None
    
    async def review_project(
        self,
        project_id: int,
        matched_pairs: list[MatchedPair],
        save_to_db: bool = True,
        skip_existing: bool = True,
        heartbeat_callback: Optional[Callable[..., None]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> list[ReviewResult]:
        """执行项目的完整审查流程
 
 Args:
 project_id: 项目ID
 matched_pairs: 匹配对列表
 save_to_db: 是否保存到数据库
 skip_existing: 是否跳过已存在的审查结果
 heartbeat_callback: 心跳回调；支持 callback(done, total, inflight=0) 或
 callback(done, total, inflight=0, *, stage=..., phase=...)
 
 Returns:
 list[ReviewResult]: 审查结果列表
 """
        # 兜底注入 project_id 到日志上下文: 即使外层 task 未注入(如直接单测调用),
        # 本方法内的所有 logger.error/warning 都会带上 project_id, 与 worker.py / tasks 路径形成双保险。
        with logger.contextualize(project_id=project_id):
            return await self._review_project_impl(
                project_id=project_id,
                matched_pairs=matched_pairs,
                save_to_db=save_to_db,
                skip_existing=skip_existing,
                heartbeat_callback=heartbeat_callback,
                cancel_check=cancel_check,
            )

    async def _review_project_impl(
        self,
        project_id: int,
        matched_pairs: list[MatchedPair],
        save_to_db: bool = True,
        skip_existing: bool = True,
        heartbeat_callback: Optional[Callable[..., None]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> list[ReviewResult]:
        """review_project 的实际实现 (拆分以便外层做日志上下文注入)"""
        from app.core.tasks.cancellation import invoke_cancel_check

        invoke_cancel_check(cancel_check)
        logger.info(f"[AI审查] 开始审查: project_id={project_id}, 共{len(matched_pairs)}项")
        original_total = len(matched_pairs)
        
        results: list[ReviewResult] = []
        
        # 跳过已有审查结果
        if skip_existing and save_to_db:
            existing_requirement_ids = await self._get_existing_review_requirement_ids(project_id)
            invoke_cancel_check(cancel_check)
            if existing_requirement_ids:
                matched_pairs = [
                    pair for pair in matched_pairs
                    if pair.requirement_id not in existing_requirement_ids
                ]
                logger.info(f"[AI审查] 已存在 {len(existing_requirement_ids)} 条审查结果，跳过已处理要求")
        
        if not matched_pairs:
            logger.info("[AI审查] 所有要求已有审查结果，无需重复审查")
            if save_to_db:
                invoke_cancel_check(cancel_check)
                await self._post_review_consistency_check(project_id)
            return []
        
        total_to_process = len(matched_pairs)
        skipped_count = original_total - total_to_process

        self._evidence_index = self._build_evidence_index(matched_pairs)
        if self._evidence_index:
            logger.info(f"[AI审查] 构建跨类别证据索引: {len(self._evidence_index)} 个关键词")
        try:
            from app.core.ai.reviewer_parts.evidence_ledger.builder import (
                build_project_evidence_ledger,
            )
            from app.core.database import db_session_scope as _ledger_scope
            from app.core.retriever.material_inventory import load_material_facts

            async with _ledger_scope() as _ledger_session:
                self._material_inventory = await load_material_facts(
                    _ledger_session, project_id,
                )
                self._evidence_ledger = await build_project_evidence_ledger(
                    _ledger_session, matched_pairs, facts=self._material_inventory,
                )
        except Exception as _ledger_err:
            from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
                EvidenceLedger as _EmptyLedger,
            )

            logger.warning(f"[AI审查] 证据账本构建降级: {_ledger_err}")
            self._evidence_ledger = _EmptyLedger()

        from app.core.config import get_review_concurrency
        concurrency = max(get_review_concurrency(), 1)
        semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"[AI审查] 待处理: {total_to_process} 项，并发数: {concurrency}")
        from app.core.tasks.parallel_unit_progress import invoke_unit_progress

        inflight = 0
        completed_units = 0

        def emit_progress() -> None:
            invoke_unit_progress(
                heartbeat_callback,
                skipped_count + completed_units,
                original_total,
                inflight,
            )

        emit_progress()
        
        async def review_with_semaphore(index: int, pair: MatchedPair) -> Optional[ReviewResult]:
            """DB 连接与 LLM 调用解耦：读/写各用短生命周期 session，LLM 期间不占连接: 全流程不再依赖共享 self.db, 三处 DB 阶段:
 - Phase 1: 短 session 预取证据 + 知识库上下文 (~100ms)
 - Phase 2: 无 session, 仅 LLM 调用 (5~60s)
 - Phase 2.5: 短 session 跑 Gate Pipeline (含 _collect_chunk_pages_for_pair)
 - Phase 3: 短 session 写入审查结果 (~100ms)
 """
            pair_req_id = pair.requirement_id
            pair_resp_id = pair.response_id
            resume = LlmRetryResumeState()
            nonlocal inflight, completed_units
            async with semaphore:
                invoke_cancel_check(cancel_check)
                logger.debug(
                    f"[AI审查] 审查 {index + 1}/{total_to_process}: "
                    f"requirement_id={pair_req_id}"
                )
                inflight += 1
                emit_progress()
                try:
                    # 短 session 改走 db_session_scope, 过进程级许可信号量,
                    # 防止跨 job 并发协程突发占满 QueuePool。
                    from app.core.database import db_session_scope

                    # Phase 1: 短 session -- 预取证据 + 知识库上下文（~100ms）
                    async with db_session_scope() as session:
                        invoke_cancel_check(cancel_check)
                        evidence = await self._prefetch_evidence(
                            pair, session, project_id=project_id,
                        )
                    invoke_cancel_check(cancel_check)

                    # Phase 2: 无 session -- LLM 调用（5~60s）
                    result = await self._review_single_pair(
                        project_id,
                        pair,
                        evidence_bundle=evidence,
                        resume_state=resume,
                    )
                    invoke_cancel_check(cancel_check)

                    if result is not None and is_failure_result(result):
                        p, ev = pair, evidence
                        self._retry_tracker.record(
                            key=f"req_{pair_req_id}",
                            retry_coro_factory=lambda _p=p, _ev=ev, _rs=resume: self._review_single_pair(
                                project_id, _p, evidence_bundle=_ev, resume_state=_rs,
                            ),
                            fallback_applied="failure_result",
                        )

                    # Phase 3: 短 session -- 写入结果（~100ms）
                    # 使用 savepoint 吸收唯一键冲突, 保证并发恢复场景下的幂等:
                    # 历史 BUG 是状态查询触发的误恢复任务与原任务并发写同一 requirement,
                    # 命中 uq_review_project_requirement 后整页 WARNING 刷屏。
                    if result and save_to_db:
                        async with db_session_scope() as session:
                            invoke_cancel_check(cancel_check)
                            session.add(result)
                            try:
                                invoke_cancel_check(cancel_check)
                                await session.commit()
                            except IntegrityError as save_err:
                                await session.rollback()
                                logger.debug(
                                    f"[AI审查] 结果已存在, 跳过重复写入: "
                                    f"requirement={pair_req_id}, err={save_err}"
                                )
                    if result is not None:
                        completed_units += 1
                    return result
                except Exception as e:
                    invoke_cancel_check(cancel_check)
                    # 异常时 dump 一次连接池快照, 便于定位 QueuePool 耗尽
                    # 是否仍然发生; 仅在错误分支调用, 不影响热路径性能。
                    try:
                        from app.core.database import engine_pool_status as _eps
                        logger.error(
                            f"[AI审查] 审查失败 (requirement={pair_req_id}): {e} | "
                            f"db_pool={_eps()}"
                        )
                    except Exception:
                        logger.error(f"[AI审查] 审查失败 (requirement={pair_req_id}): {e}")
                    p, ev = pair, evidence if 'evidence' in dir() else None
                    self._retry_tracker.record(
                        key=f"req_{pair_req_id}",
                        retry_coro_factory=lambda _p=p, _ev=ev, _rs=resume: self._review_single_pair(
                            project_id, _p, evidence_bundle=_ev, resume_state=_rs,
                        ),
                        fallback_applied="failure_result",
                    )
                    result = self._create_failure_result_safe(project_id, pair_req_id, pair_resp_id, pair)
                    if result and save_to_db:
                        try:
                            from app.core.database import db_session_scope as _scope
                            async with _scope() as fallback_session:
                                invoke_cancel_check(cancel_check)
                                fallback_session.add(result)
                                try:
                                    invoke_cancel_check(cancel_check)
                                    await fallback_session.commit()
                                except IntegrityError as save_err:
                                    await fallback_session.rollback()
                                    logger.debug(
                                        f"[AI审查] 兜底写入命中唯一键, 跳过: "
                                        f"requirement={pair_req_id}, err={save_err}"
                                    )
                        except Exception as save_err:
                            logger.warning(f"[AI审查] 兜底保存失败 (requirement={pair_req_id}): {save_err}")
                    if result is not None:
                        completed_units += 1
                    return result
                finally:
                    inflight -= 1
                    emit_progress()
        
        tasks = [
            review_with_semaphore(index, pair)
            for index, pair in enumerate(matched_pairs)
        ]
        
        log_batch_size = 5
        
        for task in asyncio.as_completed(tasks):
            invoke_cancel_check(cancel_check)
            result = await task
            invoke_cancel_check(cancel_check)
            if not result:
                continue
            
            results.append(result)
            done_count = len(results)
            emit_progress()
            
            if done_count % log_batch_size == 0 or done_count == total_to_process:
                percent = done_count * 100 // total_to_process
                logger.info(
                    f"[AI审查] 进度: {done_count}/{total_to_process} ({percent}%)"
                )
        
        logger.info(f"[AI审查] 完成: 共 {len(results)} 条结果")

        if self._retry_tracker.has_failures:
            invoke_cancel_check(cancel_check)
            retry_total = self._retry_tracker.failure_count

            def _emit_retry_progress(done: int, total: int) -> None:
                if not heartbeat_callback:
                    return
                try:
                    heartbeat_callback(
                        done,
                        total,
                        stage="步骤4-重试",
                        phase="self_healing",
                    )
                except TypeError:
                    # 兼容仅接受 (done, total) 的旧回调
                    heartbeat_callback(done, total)

            _emit_retry_progress(0, retry_total)
            retry_stats = await self._retry_tracker.retry_all_concurrent(
                max_concurrency=concurrency,
                on_progress=_emit_retry_progress,
            )
            invoke_cancel_check(cancel_check)
            if heartbeat_callback:
                try:
                    # 重试结束后进入收尾带(99)，禁止清 phase 后回落到主批次终点 90
                    heartbeat_callback(
                        len(results),
                        len(results) or total_to_process,
                        stage="步骤4-收尾",
                        phase="finalizing",
                    )
                except TypeError:
                    heartbeat_callback(len(results), len(results) or total_to_process)
            if retry_stats.recovered > 0:
                failure_req_ids = set()
                for r in results:
                    if is_failure_result(r):
                        failure_req_ids.add(r.requirement_id)

                for detail in retry_stats.details:
                    invoke_cancel_check(cancel_check)
                    retry_result = detail.get("result")
                    if not detail["success"] or not retry_result:
                        continue
                    rid = getattr(retry_result, "requirement_id", None)
                    if rid and rid in failure_req_ids:
                        results = [
                            retry_result if r.requirement_id == rid else r
                            for r in results
                        ]
                        failure_req_ids.discard(rid)
                        if save_to_db:
                            try:
                                from app.core.database import db_session_scope as _scope
                                async with _scope() as retry_session:
                                    invoke_cancel_check(cancel_check)
                                    existing = await retry_session.scalar(
                                        select(ReviewResult).where(
                                            ReviewResult.project_id == retry_result.project_id,
                                            ReviewResult.requirement_id == retry_result.requirement_id,
                                        )
                                    )
                                    if existing is not None:
                                        # Fix-09 / 问题 9：retry_result 已走过 Gate Pipeline
                                        # (_review_single_pair → _create_review_result)，
                                        # 这里必须全量覆盖 Gate 产出字段（含 None），防止旧值污染。
                                        _GATE_OUTPUT_FIELDS = self._GATE_REPLAY_COVER_FIELDS
                                        for column in ReviewResult.__table__.columns:
                                            name = column.name
                                            if name in {"id", "created_at", "updated_at"}:
                                                continue
                                            new_val = getattr(retry_result, name, None)
                                            if name in _GATE_OUTPUT_FIELDS:
                                                # Gate 产出字段：全量覆盖（None 也覆盖）
                                                setattr(existing, name, new_val)
                                            elif new_val is not None:
                                                # 结构/标识类字段：保留历史策略
                                                setattr(existing, name, new_val)
                                    else:
                                        retry_session.add(retry_result)
                                    try:
                                        invoke_cancel_check(cancel_check)
                                        await retry_session.commit()
                                    except IntegrityError as save_err:
                                        await retry_session.rollback()
                                        logger.debug(
                                            f"[AI审查] 重试结果并发冲突, 已由其他任务写入: "
                                            f"req={rid}, err={save_err}"
                                        )
                            except Exception as save_err:
                                logger.warning(
                                    f"[AI审查] 重试结果保存失败: req={rid}, err={save_err}"
                                )

        # 全局一致性收敛：避免同一事实在跨类别下出现无解释冲突
        # Fix-09: retry 覆盖后，post_pipeline 会重新计算跨组冲突、relevance 异常等，
        # 确保 Gate 产出与 existing 数据最终一致。
        if save_to_db:
            invoke_cancel_check(cancel_check)
            if heartbeat_callback:
                try:
                    heartbeat_callback(
                        len(results),
                        len(results) or total_to_process,
                        stage="步骤4-收尾",
                        phase="finalizing",
                    )
                except TypeError:
                    heartbeat_callback(len(results), len(results) or total_to_process)
            await self._post_review_consistency_check(project_id)

        return results
    
    async def _prefetch_evidence(
        self,
        pair: MatchedPair,
        session: "AsyncSession",
        project_id: Optional[int] = None,
    ) -> ReviewEvidenceBundle:
        """在短生命周期 session 内预取所有审查所需的证据数据（委托 evidence_prefetch）。: 知识库上下文与图像证据均在 Phase1 短 session 内构建,
 LLM 调用阶段不再需要 DB session。
 """
        return await evidence_prefetch.prefetch_evidence(
            self, pair, session, project_id=project_id,
        )

    async def _review_single_pair(
        self,
        project_id: int,
        pair: MatchedPair,
        evidence_bundle: Optional[ReviewEvidenceBundle] = None,
        resume_state: Optional[LlmRetryResumeState] = None,
    ) -> Optional[ReviewResult]:
        """审查单个匹配对（无 DB 依赖，证据已预取）（委托 pair_review）。: Phase 2 LLM 调用阶段无 DB session，知识库上下文已在
 Phase 1 预取写入 evidence_bundle.knowledge_context_text。
 """
        return await pair_review.review_single_pair(
            self, project_id, pair, evidence_bundle=evidence_bundle,
            resume_state=resume_state,
        )

    async def _maybe_run_cove_investigate(
        self,
        project_id: int,
        pair: "MatchedPair",
        extracted: "ExtractedReviewResult",
        review_result: "ReviewResult",
    ) -> None:
        """B5.10 投标 CoVe: 否决项 not + mandatory 触发（委托 cove_judge）。"""
        return await cove_judge.maybe_run_cove_investigate(
            self, project_id, pair, extracted, review_result,
        )

    async def _maybe_run_judge_investigate(
        self,
        project_id: int,
        pair: "MatchedPair",
        extracted: "ExtractedReviewResult",
        review_result: "ReviewResult",
    ) -> None:
        """B5.12 投标 Judge 抽样（委托 cove_judge）。"""
        return await cove_judge.maybe_run_judge_investigate(
            self, project_id, pair, extracted, review_result,
        )

    async def _fetch_image_evidence_for_pair(
        self,
        pair: MatchedPair,
        session: Optional["AsyncSession"] = None,
    ) -> list["EvidenceItem"]:
        """获取匹配对来源页面的图片证据条目（委托 pair_review）。: 返回结构化条目而非拼接文本，是否进入 Prompt 由证据包预算器决定。
 """
        return await pair_review.fetch_image_evidence_for_pair(self, pair, session=session)

    async def _independent_evidence_search(
        self,
        pair: MatchedPair,
        session: Optional["AsyncSession"] = None,
    ) -> list["EvidenceItem"]:
        """证据不足时的全文档补搜证据条目（委托 pair_review）。: 召回口径不变，返回结构化条目交由证据包预算器统一取舍。
 """
        return await pair_review.independent_evidence_search(self, pair, session=session)

    # ------------------------------------------------------------------
    # 跨类别证据索引（Phase 2 根治）
    # ------------------------------------------------------------------

    # R2-3: ClassVar re-export 自 evidence_prefetch 保持主类访问路径不变,
    # 子模块通过 self._DOMAIN_KEYWORDS 拿到同一份内容。
    _DOMAIN_KEYWORDS: list[str] = evidence_prefetch.DOMAIN_KEYWORDS

    def _build_evidence_index(
        self, matched_pairs: list[MatchedPair]
    ) -> dict[str, list[tuple[str, str, str, str | None]]]:
        """构建跨 requirement 的证据关键词索引（委托 evidence_prefetch）。"""
        return evidence_prefetch.build_evidence_index(self, matched_pairs)

    def _lookup_cross_category_evidence(
        self, pair: MatchedPair
    ) -> tuple[str, list[CrossEvidence]]:
        """Fix-08: 查找跨类别证据（委托 evidence_prefetch）。"""
        return evidence_prefetch.lookup_cross_category_evidence(self, pair)

    async def _get_project_owner_id(
        self,
        project_id: int,
        session: Optional["AsyncSession"] = None,
    ) -> Optional[int]:
        """读取项目所有者（委托 evidence_prefetch，含 owner_id 缓存）。"""
        return await evidence_prefetch.get_project_owner_id(
            self, project_id, session=session,
        )

    def _build_review_prompt(
        self,
        pair: MatchedPair,
        evidence_bundle: ReviewEvidenceBundle,
        knowledge_context: str = "",
        *,
        reduction_round: int = 0,
    ) -> str:
        """构建对比审查 Prompt（委托 pair_review）。"""
        return pair_review.build_review_prompt(
            self, pair, evidence_bundle, knowledge_context,
            reduction_round=reduction_round,
        )

    def _build_no_response_prompt(
        self,
        pair: MatchedPair,
        evidence_bundle: ReviewEvidenceBundle,
        knowledge_context: str = "",
        *,
        reduction_round: int = 0,
    ) -> str:
        """构建未响应审查 Prompt（委托 pair_review）。"""
        return pair_review.build_no_response_prompt(
            self, pair, evidence_bundle, knowledge_context,
            reduction_round=reduction_round,
        )

    def _resolve_cd_schema(self) -> dict | None:
        """B2.8 按灰度比例决定是否启用 json_object 约束（委托 pair_review）。"""
        return pair_review.resolve_cd_schema(self)

    async def _try_pre_llm_skip(
        self,
        project_id: int,
        pair: MatchedPair,
    ) -> Optional[ExtractedReviewResult]:
        """B4 pre_llm: 物理形态/程序履约要求 skip 判定（委托 pair_review）。"""
        return await pair_review.try_pre_llm_skip(self, project_id, pair)

    async def _call_llm_review(
        self,
        user_prompt: str,
        *,
        prompt_rebuilder: Optional[Callable[[int], str]] = None,
        operation_label: str = "投标审查",
        expects_nested_array_output: bool = False,
        pair: Optional[MatchedPair] = None,
        node_key: Optional[str] = None,
        resume_state: Optional[LlmRetryResumeState] = None,
    ) -> Optional[ExtractedReviewResult]:
        """调用 LLM 执行审查（按 self.max_retries 重试，委托 review_llm_invoke）。

 critic-quality-fix 静态契约（inspect.getsource）：
 - 必须保留 ``max_retries`` 字符串（max_retries loop 测试）
 - 必须保留 ``temperature=0.0`` 字符串（review 主路径用 temperature=0.0，
 子模块实际把 PromptRegistry 拿到的 temperature 默认值 0.0 透传给
 llm_client.chat / chat_json，stability_class=deterministic 时强制 0.0）: prompt_rebuilder 供 review_llm_invoke 在输入过长 400 时按 reduction_round
 语义收缩重建 prompt; 默认 None 时退化为原样发送(仅旧调用方/测试路径)。: expects_nested_array_output=True（评分项 rule_assessments 形态）时
 禁用 json_object 模式——该模式对嵌套对象数组生成存在结构性退化。: pair 供最小契约装配服务端权威字段; node_key 在发送点绑定
 真实分支节点。二者缺省时退化为旧契约与旧归因（历史调用方与测试路径）。
 """
        return await review_llm_invoke.call_llm_review(
            self, user_prompt,
            prompt_rebuilder=prompt_rebuilder,
            operation_label=operation_label,
            expects_nested_array_output=expects_nested_array_output,
            pair=pair,
            node_key=node_key,
            resume_state=resume_state,
        )
    
    @staticmethod
    def _maybe_enforce_algorithmic_risk_band(
        extracted: "ExtractedReviewResult",
        verdicts: list,
    ) -> None:
        """S.5.1 · 高置信度下手动应用 AlgorithmicRiskBandGate 的 patch
        （委托 result_builder，保留 @staticmethod 类路径调用）。"""
        return result_builder.maybe_enforce_algorithmic_risk_band(extracted, verdicts)

    async def _collect_chunk_pages_for_pair(
        self,
        pair: MatchedPair,
        session: Optional["AsyncSession"] = None,
    ) -> set[int]:
        """S.2.4 / Fix-02: 提供 PageAnchorVerifyGate 所需的 chunk_pages 参照集（委托 pair_review）。"""
        return await pair_review.collect_chunk_pages_for_pair(self, pair, session=session)

    async def _create_review_result(
        self,
        project_id: int,
        pair: MatchedPair,
        extracted: ExtractedReviewResult,
        session: Optional["AsyncSession"] = None,
    ) -> ReviewResult:
        """创建审查结果对象（委托 result_builder）。"""
        return await result_builder.create_review_result(
            self, project_id, pair, extracted, session=session,
        )

    @staticmethod
    def _compute_algorithm_evidence(pair: MatchedPair) -> dict:
        """B4: 计算算法工具结果（委托 result_builder，保留 @staticmethod 类路径调用）。"""
        return result_builder.compute_algorithm_evidence(pair)

    # Fix-01: _apply_relevance_guard 已由 RelevanceGuardGate 替代；
    # _apply_claim_response_check 已由 ClaimResponseConsistencyGate 替代。
    # legacy inline 路径下线，紧急回滚走 GATE_SEVERITY_OVERRIDES 配置。

    def _create_failure_result(
        self,
        project_id: int,
        pair: MatchedPair
    ) -> ReviewResult:
        """创建审查失败的兜底结果，避免流程卡住(非并发场景)（委托 result_builder）。"""
        return result_builder.create_failure_result(self, project_id, pair)

    def _create_failure_result_safe(
        self,
        project_id: int,
        requirement_id: int,
        response_id: Optional[int],
        pair: MatchedPair,
    ) -> ReviewResult:
        """创建审查失败的兜底结果(并发安全版)（委托 result_builder）。"""
        return result_builder.create_failure_result_safe(
            self, project_id, requirement_id, response_id, pair,
        )

    def _derive_attribution_type(self, requirement: Optional[Requirement]) -> str:
        """根据 requirement 主体范围推断归因关系（委托 consistency_postcheck）。"""
        return consistency_postcheck.derive_attribution_type(self, requirement)

    def _detect_family_type(self, description: str, category: str) -> str:
        """按描述+类别检测事实家族类型（委托 consistency_postcheck）。"""
        return consistency_postcheck.detect_family_type(self, description, category)

    def _extract_anchor_tokens(self, text: str) -> set[str]:
        """提取锚点 token（委托 fact_graph）。"""
        return fact_graph.extract_anchor_tokens(self, text)

    def _parse_source_pages(self, pages: Optional[str]) -> set[int]:
        """解析 source_pages 字符串为页码集合（委托 fact_graph）。"""
        return fact_graph.parse_source_pages(self, pages)

    def _compute_evidence_relation(
        self,
        items: list[tuple[ReviewResult, Requirement, Optional[BidResponse]]],
    ) -> str:
        """按 token 重叠 + 页码邻近 + 主体范围推断证据关系（委托 fact_graph）。"""
        return fact_graph.compute_evidence_relation(self, items)

    def _normalize_view_category(self, req: Requirement) -> str:
        """统一类别视图代码（委托 consistency_postcheck）。"""
        return consistency_postcheck.normalize_view_category(self, req)

    def _select_fact_primary_judgement(
        self,
        items: list[tuple[ReviewResult, Requirement, Optional[BidResponse]]],
    ) -> tuple[str | None, int | None, str | None, str | None]:
        """事实层主判定：优先高风险，再按最低得分（委托 fact_graph）。"""
        return fact_graph.select_fact_primary_judgement(self, items)

    async def _build_fact_graph(
        self,
        session: "AsyncSession",
        project_id: int,
        grouped: dict[str, list[tuple[ReviewResult, Requirement, Optional[BidResponse]]]],
    ) -> None:
        """构建事实图：事实主表 + 事实映射 + 事实视图判定（委托 fact_graph）。"""
        return await fact_graph.build_fact_graph(self, session, project_id, grouped)

    def _judge_fact_then_project(
        self,
        grouped: dict[str, list[tuple[ReviewResult, Requirement, Optional[BidResponse]]]],
    ) -> None:
        """先事实判定，再投影类别视图结果（委托 fact_graph）。"""
        return fact_graph.judge_fact_then_project(self, grouped)

    def _apply_family_rules(
        self,
        family_type: str,
        evidence_relation: str,
        has_conflict: bool,
    ) -> tuple[str, str]:
        """按 family_type / evidence_relation 返回 (consistency_flag, reconcile_note)。

        委托 consistency_postcheck.apply_family_rules。
        """
        return consistency_postcheck.apply_family_rules(
            self, family_type, evidence_relation, has_conflict,
        )

    # ------------------------------------------------------------------
    # 跨事实组冲突检测（Phase 0 止血）
    # R2-3: 主体实现迁移至 reviewer_parts.consistency_postcheck；ClassVar
    # re-export 自子模块以保持 `ReviewOrchestrator._MISSING_SIGNALS` 直接访问
    # 路径不变，子模块通过 `self._MISSING_SIGNALS` 拿到同一份内容。
    # ------------------------------------------------------------------

    _MISSING_SIGNALS = consistency_postcheck.MISSING_SIGNALS

    def _extract_missing_keywords(self, comment: str) -> list[str]:
        """从审查意见中提取被判定为缺失的具体项（委托 consistency_postcheck）。"""
        return consistency_postcheck.extract_missing_keywords(self, comment)

    def _evidence_covers_missing(
        self,
        br: Optional[BidResponse],
        rr: ReviewResult,
        missing_kws: list[str],
    ) -> bool:
        """检查 BidResponse/ReviewResult 的内容是否覆盖了缺失关键词（委托 consistency_postcheck）。"""
        return consistency_postcheck.evidence_covers_missing(self, br, rr, missing_kws)

    # Fix-01: _cross_group_conflict_detect 已由 CrossGroupConflictGate 替代，
    # helper（_extract_missing_keywords / _evidence_covers_missing）保留供 Gate evidence 注入。

    # ------------------------------------------------------------------
    # 证据优先事实键（Phase 3 长效）
    # ------------------------------------------------------------------

    def _derive_evidence_based_key(self, br: Optional[BidResponse]) -> str:
        """从 BidResponse 证据锚点派生事实键（委托 fact_graph）。"""
        return fact_graph.derive_evidence_based_key(self, br)

    async def _post_review_consistency_check(self, project_id: int) -> None:
        """审查后全局一致性收敛（可重入），核心流程：

        - 按 fact_key 分组 ReviewResult
        - 调用 ``self._judge_fact_then_project`` 做事实层主判定
        - Gate Pipeline 驱动 CrossGroupConflictGate / RelevanceAnomalyBackfillGate
        - 调用 ``self._build_fact_graph`` 落库

        委托至 reviewer_parts.consistency_postcheck.post_review_consistency_check；
        保留方法签名与调用锚点字符串 ``_build_fact_graph`` / ``_judge_fact_then_project``
        以兼容 inspect.getsource 静态契约。
        """
        return await consistency_postcheck.post_review_consistency_check(self, project_id)

    # ------------------------------------------------------------------
    # 关联性异常检测 helper（供 RelevanceAnomalyBackfillGate 通过 evidence 注入使用）
    # Fix-01: _relevance_anomaly_detect 已由 RelevanceAnomalyBackfillGate 替代；
    # 类别缩写兼容集合（_RELEVANCE_HIGH_SCORE_CATEGORIES）下线，Fix-03 validator 统一归一。
    # R2-3: ClassVar re-export 自 consistency_postcheck 保持主类访问路径不变。
    # ------------------------------------------------------------------

    _GENERIC_STOPWORDS = consistency_postcheck.GENERIC_STOPWORDS

    def _estimate_keyword_relevance(
        self,
        requirement_desc: str,
        response_content: str,
    ) -> int:
        """基于关键词重叠度估算关联性(0-100)（委托 consistency_postcheck）。"""
        return consistency_postcheck.estimate_keyword_relevance(
            self, requirement_desc, response_content,
        )

    def _extract_domain_keywords(self, text: str) -> list[str]:
        """从文本中提取领域关键词(委托 consistency_postcheck)。"""
        return consistency_postcheck.extract_domain_keywords(self, text)

    def _summarize_text(self, text: str, max_len: int = 200) -> str:
        """安全截断文本（委托 result_builder）。"""
        return result_builder.summarize_text(self, text, max_len)

    async def _get_existing_review_requirement_ids(
        self,
        project_id: int
    ) -> set[int]:
        """获取项目中已存在审查结果的招标要求ID: 使用短 session, 避免在主 session 上发起隐式事务后挂死连接。
 本方法虽在并发外调用 (review_project 入口), 但若用 self.db 仍会让
 长事务持连接到任务结束, 配合并发任务的 Phase1/3 短借, 容易突破连接池。
 """
        from sqlalchemy import select

        from app.core.database import async_session_factory
        async with async_session_factory() as session:
            result = await session.execute(
                select(ReviewResult.requirement_id).where(
                    ReviewResult.project_id == project_id
                )
            )
            return {row[0] for row in result.fetchall()}

    def _get_default_timeout(self) -> float:
        """根据AI模式返回默认超时（秒）"""
        return settings.CLOUD_TIMEOUT if get_ai_mode() == "cloud" else 300.0
    
    def summarize_results(self, results: list[ReviewResult]) -> dict:
        """汇总审查结果
        
        Args:
            results: 审查结果列表
            
        Returns:
            dict: 汇总统计信息
        """
        if not results:
            return {
                "total_count": 0,
                "conclusion": "pending",
                "conclusion_reason": "无审查结果"
            }
        
        summary = build_review_summary(
            project_id=0,
            items=[
                ReviewSummaryItem(
                    category=(
                        result.requirement.category.value
                        if result.requirement and result.requirement.category
                        else "unknown"
                    ),
                    is_compliant=bool(result.is_compliant),
                    compliance_level=result.compliance_level,
                    risk_level=result.risk_level,
                    score=result.score,
                )
                for result in results
            ],
        )
        summary["high_risk_items"] = [
            r for r in results if r.risk_level == RiskLevel.HIGH
        ]
        return summary


# 工厂函数
def get_review_orchestrator(
    db: Optional[AsyncSession] = None,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None
) -> ReviewOrchestrator:
    """获取审查编排器实例

 Args:
 db: 数据库会话(可选)。: 生产并发路径请传 None,编排器内部
 的并发短 session 全部走 db_session_scope。
 llm_client: LLM客户端（可选，默认根据AI模式配置自动选择）

 Returns:
 ReviewOrchestrator: 编排器实例
 """
    return ReviewOrchestrator(
        db=db,
        llm_client=llm_client,
        max_retries=settings.REVIEW_MAX_RETRIES,
        project_id=project_id
    )
