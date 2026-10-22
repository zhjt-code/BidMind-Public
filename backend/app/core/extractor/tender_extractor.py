# Compatibility facade: extracted helpers remain available to callers and tests.
# ruff: noqa: F401
"""招标要求提取器

从招标文件中提取结构化的招标要求。

流程：
1. 获取招标文档的所有分块
2. 并行对多个分块调用 LLM 提取要求（提高速度）
3. 合并去重所有分块的提取结果
4. 存入数据库等待人工确认
"""

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client
from app.core.ai.prompts.tender_extract import (
    get_category_merge_decision_prompt,
    get_reflection_prompt,
    get_secondary_merge_decision_prompt,
    get_tender_extract_prompt,
)
from app.core.config import settings
from app.core.extractor.relevance_resolver import resolve_review_relevance
from app.core.extractor.tender_extractor_parts import (
    dedup_stage as _dedup_stage,
)
from app.core.extractor.tender_extractor_parts import (
    extraction_stage as _extraction_stage,
)
from app.core.extractor.tender_extractor_parts import (
    merge_category_stage as _merge_category_stage,
)
from app.core.extractor.tender_extractor_parts import (
    quality_check as _quality_check,
)
from app.core.extractor.tender_extractor_parts import (
    secondary_merge_stage as _secondary_merge_stage,
)
from app.core.extractor.tender_extractor_parts.candidate_gate import (
    CandidateEmbeddingBuffer,
    partition_requirements_with_embeddings,
)
from app.core.extractor.tender_extractor_parts.extraction_units import (
    build_lead_context_map,
    select_extraction_chunks,
)
from app.core.extractor.tender_extractor_parts.near_duplicate_premerge import (
    collapse_near_duplicates,
)
from app.core.extractor.tender_extractor_parts.performance_metrics import (
    TenderExtractionMetrics,
)
from app.core.extractor.tender_extractor_parts.waiver_fold import (
    fold_delegations_into_waivers,
)
from app.core.observability.instrument import instrument as _observability_instrument
from app.core.retriever.facet_vocab import FACET_SUBJECT_SCOPE, FACET_TRIGGER_WORDS
from app.core.review_contracts import serialize_json_list
from app.models.chunk import Chunk
from app.models.document import Document, DocumentType
from app.models.requirement import Requirement, RequirementCategory
from app.schemas.requirement import ExtractedRequirement

# 分类代码到枚举的映射
CATEGORY_MAP = {
    "QUAL": RequirementCategory.QUAL,
    "TECH": RequirementCategory.TECH,
    "COMM": RequirementCategory.COMM,
    "PERF": RequirementCategory.PERF,
    "STAFF": RequirementCategory.STAFF,
    "FORMAT": RequirementCategory.FORMAT,
}


# ------------------------------------------------------------------
# 模块级推断函数（供外部模块复用，如 requirements API 编辑后重算）
# ------------------------------------------------------------------

def infer_subject_scope(description: str, keywords: list[str] | None = None) -> str:
    """根据描述推断主体范围。

    先检测"法定代表人"等长词归入 personnel，再检测"法人"短词归入
    enterprise，避免"法定代表人"被误判为企业主体。
    """
    text = (description or "") + " " + " ".join(keywords or [])
    lower = text.lower()

    _PERSONNEL_REPRESENTATIVE_KEYWORDS = [
        "法定代表人", "法人代表", "单位负责人", "授权代表",
        "委托代理人", "授权委托",
    ]
    _PERSONNEL_ROLE_KEYWORDS = [
        "项目经理", "项目负责人", "技术负责人", "安全员",
        "人员", "任职",
    ]

    has_representative = any(k in text for k in _PERSONNEL_REPRESENTATIVE_KEYWORDS)
    has_role = any(k in text for k in _PERSONNEL_ROLE_KEYWORDS) or "manager" in lower
    has_personnel = has_representative or has_role

    has_enterprise = any(k in text for k in [
        "投标人", "企业", "公司", "营业执照",
    ]) or "company" in lower
    if not has_enterprise and not has_representative:
        if "法人" in text:
            has_enterprise = True

    if has_enterprise and has_personnel:
        return "both"
    if has_personnel:
        return "personnel"
    if has_enterprise:
        return "enterprise"
    return "generic"


def infer_topic(description: str, keywords: list[str] | None = None) -> str:
    """根据描述推断主题。"""
    text = (description or "") + " " + " ".join(keywords or [])
    if any(k in text for k in ["业绩", "类似项目", "合同金额", "项目经验"]):
        return "performance"
    if any(k in text for k in [
        "资质", "营业执照", "证书", "资格",
        "失信", "信用", "重大违法", "黑名单", "不良行为",
        "授权委托", "法定代表人",
    ]):
        return "qualification"
    if any(k in text for k in ["报价", "价格", "保证金"]):
        return "commercial"
    if any(k in text for k in ["技术", "测试", "参数", "方案"]):
        return "technical"
    return "generic"


def extract_fact_signature(description: str) -> str:
    """抽取事实签名（合同/报告/发票/金额/年份/项目名词）。"""
    text = description or ""
    contract_match = re.findall(r"(?:合同(?:编号|号)?[:：]?\s*)?([A-Za-z0-9\-]{6,})", text)
    report_match = re.findall(r"(?:审计(?:报告)?(?:编号|号)?[:：]?\s*)([A-Za-z0-9\-]{4,})", text)
    invoice_match = re.findall(r"(?:发票(?:号码|号)?[:：]?\s*)([A-Za-z0-9\-]{6,})", text)
    primary_id = next(
        (
            item
            for item in [
                contract_match[0] if contract_match else "",
                report_match[0] if report_match else "",
                invoice_match[0] if invoice_match else "",
            ]
            if item
        ),
        "",
    )
    money_match = re.findall(r"\d+(?:\.\d+)?\s*(?:亿|万元|万|元)", text)
    money = ",".join(money_match[:3])
    years = re.findall(r"20\d{2}", text)
    years_key = ",".join(sorted(set(years))[:3])
    nouns = re.findall(r"[\u4e00-\u9fff]{2,8}", text)
    stop_words = {
        "投标人", "提供", "要求", "项目", "合同", "业绩", "证明", "材料", "满足",
        "项目经理", "技术负责人", "安全员", "人员", "任职", "企业", "公司",
        "审计报告", "发票", "税务", "信息库", "编号", "报告",
    }
    key_terms = [w for w in nouns if w not in stop_words][:5]
    return "|".join([primary_id, money, years_key, ",".join(key_terms)])


def infer_canonical_key(description: str, keywords: list[str] | None = None) -> str:
    """构建跨类别事实键（不包含主体维度）。"""
    topic = infer_topic(description, keywords)
    signature = extract_fact_signature(description)
    if not signature.strip():
        signature = (description or "").strip()[:80]
    raw = f"{topic}|{signature}"
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]
    return f"{topic}:{digest}"


class TenderExtractor:
    """招标要求提取器
    
    从招标文件的分块中提取结构化要求，供人工确认。
    
    示例:
        >>> extractor = TenderExtractor(db, llm_client)
        >>> requirements = await extractor.extract_from_document(doc_id)
        >>> print(f"提取了 {len(requirements)} 条要求")
    """
    
    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        concurrency: Optional[int] = None,
        max_retries: int = 3,
        progress_callback: Optional[callable] = None,
        project_id: Optional[int] = None
    ):
        """初始化招标要求提取器
        
        Args:
            db: 数据库会话
            llm_client: LLM客户端（可选，默认根据AI模式配置自动选择）
            concurrency: 并行调用LLM的并发数（可选，默认根据通道数动态计算）
            max_retries: LLM 调用最大重试次数
            progress_callback: 进度回调函数，签名为 callback(stage: str, current: int, total: int, message: str, extra: dict | None = None)
        """
        from app.core.config import get_review_concurrency
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self.concurrency = concurrency if concurrency is not None else get_review_concurrency()
        self.max_retries = max_retries
        self.progress_callback = progress_callback
        self._cancel_check: Callable[[], None] | None = None
        self._fallback_events: list[dict[str, Any]] = []
        self._stage_failures: list[dict[str, Any]] = []
        self._extraction_metrics: TenderExtractionMetrics | None = None
        self._chunk_cache_lock = asyncio.Lock()
        self._candidate_embedding_buffer = CandidateEmbeddingBuffer()
        # chunk.id -> 上一块引导句，extract_from_document 内填充
        self._chunk_lead_context: dict[int, str] = {}
        # 高风险原子条件召回报告，供 extract_task 收集告警
        self._mandatory_coverage_report: Any = None
    
    def _report_progress(
        self,
        stage: str,
        current: int,
        total: int,
        message: str = "",
        *,
        substage: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """上报进度；进度边界同时作为项目取消检查点。"""
        from app.core.tasks.cancellation import invoke_cancel_check

        invoke_cancel_check(self._cancel_check)
        if self.progress_callback:
            try:
                extra: dict[str, Any] = {}
                if substage:
                    extra["merging_substage"] = substage
                if meta:
                    extra["merging_meta"] = meta
                if self._extraction_metrics is not None:
                    extra["performance_metrics"] = (
                        self._extraction_metrics.to_heartbeat_meta()
                    )

                # 兼容旧签名（4参数）和新签名（5参数）
                if extra:
                    self.progress_callback(stage, current, total, message, extra)
                else:
                    self.progress_callback(stage, current, total, message)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")

    def _record_stage_failure(self, stage: str, reason: str, **meta: Any) -> None:
        event: dict[str, Any] = {"stage": stage, "reason": reason}
        if meta:
            event.update(meta)
        self._stage_failures.append(event)

    def _record_fallback(self, stage: str, reason: str, **meta: Any) -> None:
        event: dict[str, Any] = {"stage": stage, "reason": reason}
        if meta:
            event.update(meta)
        self._fallback_events.append(event)

    def _sanitize_text(self, text: str) -> str:
        """清理控制字符，降低JSON序列化与解析漂移风险。"""
        if not text:
            return ""
        return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)

    def _sanitize_requirements_for_prompt(
        self,
        requirements: list[ExtractedRequirement],
        *,
        merge_mode: bool = False,
    ) -> list[dict[str, Any]]:
        sanitized: list[dict[str, Any]] = []
        for idx, req in enumerate(requirements):
            if merge_mode:
                item: dict[str, Any] = {
                    "idx": idx,
                    "category": req.category,
                    "description": self._sanitize_text(req.description),
                    "is_mandatory": req.is_mandatory,
                    "keywords": [
                        self._sanitize_text(str(k))
                        for k in (req.keywords or [])
                        if str(k).strip()
                    ],
                }
            else:
                item = req.model_dump()
                item["description"] = self._sanitize_text(item.get("description", ""))
                keywords = item.get("keywords") or []
                item["keywords"] = [self._sanitize_text(str(k)) for k in keywords if str(k).strip()]
            sanitized.append(item)
        return sanitized

    def _merge_issue_lists(self, base_issues: list[dict], extra_issues: list[dict]) -> list[dict]:
        merged: list[dict] = []
        seen: set[str] = set()
        for issue in [*(base_issues or []), *(extra_issues or [])]:
            if not isinstance(issue, dict):
                continue
            affected = issue.get("affected_indices", [])
            if not isinstance(affected, list):
                affected = []
            normalized = sorted(
                idx for idx in affected if isinstance(idx, int) and idx >= 0
            )
            signature = f"{issue.get('type', 'unknown')}|{','.join(str(i) for i in normalized)}|{issue.get('description', '')[:60]}"
            if signature in seen:
                continue
            seen.add(signature)
            cloned = dict(issue)
            cloned["affected_indices"] = normalized
            merged.append(cloned)
        return merged

    def _build_precision_meta(
        self,
        requirements: list[ExtractedRequirement],
        *,
        duplicate_issues: list[dict],
    ) -> dict[str, Any]:
        total = len(requirements)
        mandatory = sum(1 for r in requirements if r.is_mandatory)
        mandatory_ratio = round((mandatory / total), 4) if total else 0.0
        category_distribution = dict(Counter(r.category for r in requirements))
        duplicate_signals = dict(Counter(i.get("type", "unknown") for i in (duplicate_issues or [])))
        return {
            "total": total,
            "mandatory_ratio": mandatory_ratio,
            "category_distribution": category_distribution,
            "duplicate_signals": duplicate_signals,
            "fallback_path": [dict(item) for item in self._fallback_events],
            "stage_failures": [dict(item) for item in self._stage_failures],
        }

    def _should_emit_baseline_snapshot(self, project_id: int) -> bool:
        if not settings.TENDER_PRECISION_GUARD_ENABLED:
            return False
        raw = (settings.TENDER_PRECISION_BASELINE_PROJECT_IDS or "").strip()
        if not raw:
            return False
        allowed: set[int] = set()
        for token in raw.split(","):
            token = token.strip()
            if token.isdigit():
                allowed.add(int(token))
        return project_id in allowed

    def _emit_precision_baseline_snapshot(
        self,
        *,
        project_id: int,
        document_id: int,
        extracted_count: int,
        merged: list[ExtractedRequirement],
        precision_meta: dict[str, Any],
    ) -> None:
        if not self._should_emit_baseline_snapshot(project_id):
            return
        try:
            snapshot_dir = Path(settings.TENDER_PRECISION_BASELINE_DIR)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            payload = {
                "project_id": project_id,
                "document_id": document_id,
                "captured_at": ts,
                "extracted_count": extracted_count,
                "merged_count": len(merged),
                "precision_meta": precision_meta,
                "requirements": [r.model_dump() for r in merged],
            }
            out_path = snapshot_dir / f"project_{project_id}_doc_{document_id}_{ts}.json"
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("精度基线快照已输出: {}", out_path)
        except Exception as exc:
            logger.warning("精度基线快照输出失败: {}", exc)
    
    async def extract_from_document(
        self,
        document_id: int,
        save_to_db: bool = True,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[Requirement]:
        """从招标文档中提取要求
        
        Args:
            document_id: 招标文档ID
            save_to_db: 是否保存到数据库
            cancel_check: 项目取消检查点（删除/取消时抛 ProjectCancelledError）
            
        Returns:
            list[Requirement]: 提取的招标要求列表
            
        Raises:
            ValueError: 文档不存在或不是招标文件
        """
        self._cancel_check = cancel_check
        from app.core.tasks.cancellation import invoke_cancel_check

        invoke_cancel_check(self._cancel_check)
        # 1. 验证文档
        document = await self._get_tender_document(document_id)
        if not document:
            raise ValueError(f"招标文档 {document_id} 不存在")
        
        if document.doc_type != DocumentType.TENDER:
            raise ValueError(f"文档 {document_id} 不是招标文件")
        
        self._fallback_events = []
        self._stage_failures = []
        self._extraction_metrics = TenderExtractionMetrics()

        logger.info(f"开始提取招标要求: document_id={document_id}")
        self._report_progress("extracting", 0, 100, "获取文档分块")
        
        # 2. 获取文档分块
        chunks = await self._get_document_chunks(document_id)
        if not chunks:
            logger.warning(f"文档 {document_id} 没有分块")
            return []
        
        total_chunks = len(chunks)
        # 块首为列表续项时携带上一块引导句，避免情形句脱离「其投标将被拒绝」语境
        self._chunk_lead_context = build_lead_context_map(chunks)
        self._extraction_metrics.set_total_chunks(total_chunks)
        self._extraction_metrics.mark_stage_start("extracting")
        logger.info(f"文档共 {total_chunks} 个分块，开始并行提取（并发数={self.concurrency}）")
        self._report_progress("extracting", 0, total_chunks, f"开始提取，共{total_chunks}个分块")
        
        # 3. 并行提取要求（使用信号量控制并发数）
        semaphore = asyncio.Semaphore(self.concurrency)
        extracted_count = 0
        
        async def extract_with_semaphore(chunk: Chunk) -> list[ExtractedRequirement] | None:
            """带并发控制的分块提取。

            Returns:
                list[ExtractedRequirement]: 提取到的要求（可能为空列表，表示合法空页）
                None: 真实失败（异常/重试耗尽）
            """
            nonlocal extracted_count
            async with semaphore:
                try:
                    from app.core.tasks.cancellation import invoke_cancel_check

                    invoke_cancel_check(self._cancel_check)
                    result = await self._extract_from_chunk(chunk)
                    if result:
                        self._candidate_embedding_buffer.submit(result)
                    if self._extraction_metrics is not None:
                        self._extraction_metrics.record_chunk_result(
                            item_count=len(result or []),
                        )
                    extracted_count += 1
                    self._report_progress("extracting", extracted_count, total_chunks, f"提取分块 {extracted_count}/{total_chunks}")
                    return result
                except Exception as e:
                    logger.error(f"分块 {chunk.id} 提取失败: {e}")
                    if self._extraction_metrics is not None:
                        self._extraction_metrics.record_chunk_failure()
                    self._record_stage_failure(
                        "chunk_extraction",
                        f"分块 {chunk.id} 提取异常: {e}",
                        chunk_id=chunk.id,
                        page_num=chunk.page_num,
                    )
                    extracted_count += 1
                    self._report_progress("extracting", extracted_count, total_chunks, f"分块 {chunk.id} 提取失败")
                    return None
        
        # 创建所有任务
        tasks = [extract_with_semaphore(chunk) for chunk in chunks]
        
        # 并行执行所有任务
        results = await asyncio.gather(*tasks)
        
        # 合并结果 + 识别真实失败分块（区分合法空页与真实失败）
        all_extracted: list[ExtractedRequirement] = []
        failed_chunks: list[Chunk] = []
        empty_page_count = 0
        for chunk, result in zip(chunks, results):
            if result is None:
                failed_chunks.append(chunk)
            elif result:
                all_extracted.extend(result)
            else:
                empty_page_count += 1

        if empty_page_count > 0:
            logger.info(f"{empty_page_count} 个分块无新要求（合法空页，不视为失败）")

        # 真实失败分块二次补救（串行，差异化策略）
        if failed_chunks:
            logger.warning(
                f"{len(failed_chunks)} 个分块首轮提取真实失败，启动二次补救 (validation_rescue 模式)"
            )
            self._report_progress(
                "extracting", extracted_count, total_chunks,
                f"{len(failed_chunks)} 个分块提取失败，二次补救中",
            )
            for chunk in failed_chunks:
                try:
                    retry_result = await self._extract_from_chunk(
                        chunk, retry_mode="validation_rescue"
                    )
                    if retry_result is not None and len(retry_result) > 0:
                        self._candidate_embedding_buffer.submit(retry_result)
                        all_extracted.extend(retry_result)
                        logger.info(
                            f"分块 {chunk.id} 二次补救成功，提取 {len(retry_result)} 条"
                        )
                        self._record_fallback(
                            "chunk_extraction_retry",
                            f"分块 {chunk.id} 二次补救成功",
                            chunk_id=chunk.id,
                            retry_mode="validation_rescue",
                        )
                        self._stage_failures = [
                            sf for sf in self._stage_failures
                            if not (sf["stage"] in ("chunk_extraction", "chunk_item_validation")
                                    and sf.get("chunk_id") == chunk.id)
                        ]
                    elif retry_result is not None:
                        logger.debug(
                            f"分块 {chunk.id} 二次补救返回空（合法空页）(page={chunk.page_num})"
                        )
                        self._stage_failures = [
                            sf for sf in self._stage_failures
                            if not (sf["stage"] == "chunk_extraction"
                                    and sf.get("chunk_id") == chunk.id)
                        ]
                    else:
                        logger.warning(
                            f"分块 {chunk.id} 二次补救仍失败 (page={chunk.page_num})"
                        )
                except Exception as e:
                    logger.error(f"分块 {chunk.id} 二次补救异常: {e}")

        # 统计最终失败的分块（按chunk_id去重，区分错误类型）
        seen_chunk_ids: set[int] = set()
        final_failed: list[dict[str, Any]] = []
        item_validation_failures: list[dict[str, Any]] = []
        for sf in self._stage_failures:
            cid = sf.get("chunk_id")
            if sf["stage"] == "chunk_extraction":
                if cid not in seen_chunk_ids:
                    seen_chunk_ids.add(cid)
                    final_failed.append(sf)
            elif sf["stage"] == "chunk_item_validation":
                item_validation_failures.append(sf)

        if item_validation_failures:
            total_item_failures = sum(
                sf.get("failed_count", 0) for sf in item_validation_failures
            )
            logger.info(
                f"逐条 salvage 共丢弃 {total_item_failures} 条非法条目 "
                f"(涉及 {len(item_validation_failures)} 个分块)"
            )

        if final_failed:
            failed_pages = [sf.get("page_num") for sf in final_failed]
            error_types: dict[str, int] = {}
            for sf in final_failed:
                err_str = sf.get("last_error", "")
                if "literal_error" in err_str or "category" in err_str.lower():
                    etype = "category_validation"
                elif "json" in err_str.lower() or "Unterminated" in err_str:
                    etype = "json_parse"
                elif "min_length" in err_str or "description" in err_str.lower():
                    etype = "field_validation"
                else:
                    etype = "other"
                error_types[etype] = error_types.get(etype, 0) + 1

            logger.warning(
                f"最终仍有 {len(final_failed)} 个分块提取失败，"
                f"涉及页码: {failed_pages}, 错误分布: {error_types}"
            )
            self._report_progress(
                "extracting", total_chunks, total_chunks,
                f"注意: {len(final_failed)} 个分块提取失败",
                substage="chunk_failures",
                meta={
                    "failed_count": len(final_failed),
                    "failed_pages": failed_pages,
                    "error_types": error_types,
                    "item_salvage_discarded": sum(
                        sf.get("failed_count", 0) for sf in item_validation_failures
                    ),
                },
            )

        logger.info(f"初步提取 {len(all_extracted)} 条要求")

        # 3.5 高风险原子条件召回：在合并前进入同一条收敛栈，
        # 补提项与普通提取项一起过候选门 + LLM 合并 + 评审意义判定，不再单独落库。
        self._mandatory_coverage_report = None
        if settings.TENDER_MANDATORY_COVERAGE_ENABLED:
            from app.core.extractor.mandatory import recall_mandatory_conditions

            before_recall = len(all_extracted)
            all_extracted, self._mandatory_coverage_report = await recall_mandatory_conditions(
                chunks=chunks,
                extracted=all_extracted,
                llm_client=self.llm_client,
                progress_callback=self._report_progress,
            )
            if len(all_extracted) > before_recall:
                self._candidate_embedding_buffer.submit(all_extracted[before_recall:])

        if self._extraction_metrics is not None:
            self._extraction_metrics.mark_stage_end("extracting")
            self._extraction_metrics.mark_stage_start("merging")
            self._extraction_metrics.record_premerge(
                before_count=len(all_extracted),
                after_count=len(all_extracted),
            )
        self._report_progress("merging", 0, 100, f"初步提取 {len(all_extracted)} 条，开始合并去重")
        
        # 4. 合并去重
        if len(all_extracted) > 0:
            merged = await self._merge_requirements(all_extracted)
            logger.info(f"合并去重后 {len(merged)} 条要求")
            if self._extraction_metrics is not None:
                self._extraction_metrics.mark_stage_end("merging")
                self._extraction_metrics.mark_stage_start("saving")
                self._extraction_metrics.record_merge_result(
                    merged_count=len(merged),
                )
            precision_meta = getattr(self, "_latest_precision_meta", None)
            guard_kwargs: dict[str, Any] = {}
            if settings.TENDER_PRECISION_GUARD_ENABLED and isinstance(precision_meta, dict):
                guard_kwargs["substage"] = "precision_guard"
                guard_kwargs["meta"] = precision_meta
            # 观测元数据只进 extra，不单独抬高 saving current。
            # merging/saving 同属 consolidate 步，心跳按 overall 防回退：
            # 先报 20% 再报 10% 会丢掉「评审意义判定」文案。
            self._report_progress(
                "saving",
                0,
                100,
                f"合并去重后 {len(merged)} 条",
                **guard_kwargs,
            )
            if guard_kwargs:
                self._emit_precision_baseline_snapshot(
                    project_id=document.project_id,
                    document_id=document_id,
                    extracted_count=len(all_extracted),
                    merged=merged,
                    precision_meta=precision_meta,
                )
        else:
            merged = []

        # 4.5 评审意义判定 pass（整表批量判定可评审性 + 识别含分值评分项）
        # 开关控制；判定器内部对单批 LLM 失败回退规则，不阻断提取。
        if merged and settings.TENDER_RELEVANCE_PASS_ENABLED:
            self._report_progress("saving", 10, 100, "评审意义判定")
            try:
                from app.core.extractor.relevance import classify_review_relevance

                def _on_relevance(done: int, total: int) -> None:
                    current = 10 + int(70 * done / max(1, total))
                    self._report_progress(
                        "saving",
                        current,
                        100,
                        f"评审意义判定 {done}/{total} 批",
                    )

                merged = await classify_review_relevance(
                    merged,
                    self.llm_client,
                    progress_callback=_on_relevance,
                )
            except Exception as e:
                logger.warning(f"评审意义判定 pass 失败(忽略): {e}")

        # 5. 保存到数据库
        if save_to_db and merged:
            save_current = 85 if settings.TENDER_RELEVANCE_PASS_ENABLED else 50
            self._report_progress("saving", save_current, 100, "保存到数据库")
            requirements = await self._save_requirements(
                project_id=document.project_id,
                document_id=document_id,
                extracted=merged
            )
            logger.info(f"已保存 {len(requirements)} 条招标要求到数据库")
            if self._extraction_metrics is not None:
                self._extraction_metrics.mark_stage_end("saving")
            self._report_progress("completed", 100, 100, f"提取完成，共 {len(requirements)} 条")
            return requirements
        
        if self._extraction_metrics is not None:
            self._extraction_metrics.mark_stage_end("saving")
        self._report_progress("completed", 100, 100, "提取完成（无结果）")
        return []
    
    async def _get_tender_document(self, document_id: int) -> Optional[Document]:
        """获取招标文档"""
        result = await self.db.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()
    
    async def _get_document_chunks(self, document_id: int) -> list[Chunk]:
        """获取文档用于要求提取的分块。: 表行 / 评分表派生分块与同页正文重复且无管辖上下文，只服务检索与
 评分通道，不作为提取单位（见 extraction_units.select_extraction_chunks）。
 """
        result = await self.db.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
        )
        return select_extraction_chunks(list(result.scalars().all()))
    
    _VALID_CATEGORIES = frozenset({"QUAL", "TECH", "COMM", "PERF", "STAFF", "FORMAT"})

    _CATEGORY_ALIAS_MAP: dict[str, str] = {
        "GENERIC": "TECH",
        "GENERAL": "TECH",
        "OTHER": "TECH",
        "综合": "TECH",
        "通用": "TECH",
        "QUALIFICATION": "QUAL",
        "TECHNICAL": "TECH",
        "COMMERCIAL": "COMM",
        "PERFORMANCE": "PERF",
        "STAFFING": "STAFF",
    }

    def _normalize_raw_item(self, item: dict) -> dict:
        """对 LLM 返回的单条原始 dict 做低风险归一化。"""
        return _extraction_stage.normalize_raw_item(self, item)

    def _infer_category_from_content(self, item: dict) -> str | None:
        """根据 description + keywords 推断 category，低置信时返回 None。"""
        return _extraction_stage.infer_category_from_content(self, item)

    def _summarize_invalid_raw_item(
        self,
        raw: dict,
        normalized: dict,
        item_idx: int,
        item_err: ValidationError,
    ) -> dict[str, Any]:
        """把被丢弃的原始条目压缩成可展示给用户核对的摘要。"""
        return _extraction_stage.summarize_invalid_raw_item(
            self, raw, normalized, item_idx, item_err
        )

    def _parse_items_with_salvage(
        self,
        items: list[dict],
        chunk_id: int | None = None,
    ) -> tuple[list[ExtractedRequirement], list[dict[str, Any]]]:
        """逐条校验 + 局部 salvage。"""
        return _extraction_stage.parse_items_with_salvage(self, items, chunk_id)

    _VALIDATION_CORRECTION_HINT = (
        "\n\n上次输出存在字段校验错误。请严格遵守以下规则:"
        "\n1. category 只能是 QUAL/TECH/COMM/PERF/STAFF/FORMAT 之一，禁止使用 GENERIC/OTHER/GENERAL"
        "\n2. description 不得为空且长度>=5"
        "\n3. is_mandatory 必须为 true 或 false"
        "\n4. keywords 和 source_pages 必须为数组"
        "\n5. 不要输出 topic/subject_scope/canonical_key 等额外字段"
    )

    def _merge_provenance(
        self,
        into: ExtractedRequirement,
        extra: ExtractedRequirement,
    ) -> ExtractedRequirement:
        """将 ``extra`` 的来源页码 / 关键词 / 子要求 / 合并计数回并到 ``into``。"""
        return _extraction_stage.merge_provenance(self, into, extra)

    def _align_chunk_source_pages(
        self,
        items: list[ExtractedRequirement],
        chunk: Chunk,
    ) -> list[ExtractedRequirement]:
        """将单 chunk 抽取的条目 source_pages 校正到真实 chunk.page_num。"""
        return _extraction_stage.align_chunk_source_pages(self, items, chunk)

    @_observability_instrument(
        name="tender_extract_chunk_seconds",
        layer="stage",
        pillar="efficiency",
        direction="lower_better",
        business="tender",
        measure="latency",
        description="招标文件单分块提取耗时",
    )
    async def _extract_from_chunk(
        self,
        chunk: Chunk,
        *,
        retry_mode: str | None = None,
    ) -> list[ExtractedRequirement] | None:
        """从单个分块中提取要求。"""
        return await _extraction_stage.extract_from_chunk(self, chunk, retry_mode=retry_mode)

    def _local_pre_dedupe(
        self,
        requirements: list[ExtractedRequirement],
    ) -> list[ExtractedRequirement]:
        """LLM 合并前的本地快速预去重。"""
        return _extraction_stage.local_pre_dedupe(self, requirements)

    async def _merge_requirements(
        self,
        requirements: list[ExtractedRequirement]
    ) -> list[ExtractedRequirement]:
        """合并去重提取的要求（分类后独立合并 + 反思机制）
        
        流程：
        1. 按类别分组
        2. 每个类别独立调用 LLM 合并（并行）
        3. 跨类别重复检测（反思机制）
        4. 必要时进行二次合并
        
        Args:
            requirements: 原始提取的要求列表
            
        Returns:
            list[ExtractedRequirement]: 去重后的要求列表
        """
        if len(requirements) <= 3:
            return self._simple_dedupe(requirements)

        # Step 0: 本地快速预去重，减少后续 LLM 调用量
        original_count = len(requirements)
        requirements = self._local_pre_dedupe(requirements)
        requirements = _dedup_stage.terminal_exact_collapse(self, requirements)
        before_near_dup = len(requirements)
        if settings.TENDER_NEAR_DUP_PREMERGE_ENABLED:
            requirements = collapse_near_duplicates(
                requirements,
                threshold=settings.TENDER_NEAR_DUP_NGRAM_THRESHOLD,
            )
            if self._extraction_metrics is not None:
                self._extraction_metrics.record_near_dup_premerge(
                    removed_count=before_near_dup - len(requirements),
                )
        # 具体豁免优先于引用型模板句（不要求保证金 vs 按招标文件要求提交保证金）
        requirements = fold_delegations_into_waivers(requirements)
        if self._extraction_metrics is not None:
            self._extraction_metrics.record_premerge(
                before_count=original_count,
                after_count=len(requirements),
            )
        if len(requirements) < original_count:
            self._report_progress(
                "merging",
                5,
                100,
                f"本地预去重: {original_count} -> {len(requirements)}",
                substage="pre_dedupe",
            )

        logger.info(f"开始分类合并去重: 原始 {len(requirements)} 条")
        self._report_progress(
            "merging",
            10,
            100,
            "按类别分组",
            substage="category_grouping",
        )
        
        # Step 1: 按类别分组
        grouped: dict[str, list[ExtractedRequirement]] = {}
        for req in requirements:
            category = req.category
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(req)
        
        logger.info(f"按类别分组: {', '.join(f'{k}={len(v)}' for k, v in grouped.items())}")
        self._report_progress(
            "merging",
            15,
            100,
            f"分组完成，{len(grouped)}个类别",
            substage="category_grouping",
            meta={"grouped_categories": len(grouped)},
        )
        
        # Step 2: 每个类别独立调用 LLM 合并（并行，使用 as_completed 实现实时进度更新）
        merged_results: list[ExtractedRequirement] = []
        merge_jobs: list[tuple[str, list[ExtractedRequirement]]] = []
        small_groups: list[ExtractedRequirement] = []
        
        for category, reqs in grouped.items():
            if not settings.TENDER_CANDIDATE_GATE_ENABLED:
                if len(reqs) > 3:
                    merge_jobs.append((category, reqs))
                else:
                    small_groups.extend(reqs)
                continue
            partition = await partition_requirements_with_embeddings(
                reqs,
                embedding_buffer=self._candidate_embedding_buffer,
                fallback_recorder=self._record_fallback,
            )
            if self._extraction_metrics is not None:
                self._extraction_metrics.record_candidate_gate(
                    singletons=len(partition.bypass_requirements),
                    components=len(partition.candidate_groups),
                    embed_wait_ms=self._candidate_embedding_buffer.wait_ms,
                )
            small_groups.extend(partition.bypass_requirements)
            for candidate_group in partition.candidate_groups:
                if len(candidate_group) > 1:
                    merge_jobs.append((category, candidate_group))
                else:
                    small_groups.extend(candidate_group)
        
        # 并行执行分类合并。jobs 是候选门控切出的合并组，不是正式 6 类。
        merge_group_count = len(merge_jobs)
        self._report_progress(
            "merging",
            15,
            100,
            f"执行分类合并（{merge_group_count}组）",
            substage="category_merge",
            meta={
                "total_merge_groups": merge_group_count,
                "grouped_categories": len(grouped),
            },
        )
        if merge_jobs:
            fracs = [0.0] * merge_group_count
            best_progress = [15]

            def emit_merge_progress(
                idx: int,
                frac: float,
                inner_done: int | None,
                inner_total: int | None,
            ) -> None:
                fracs[idx] = max(fracs[idx], frac)
                current = 15 + int(55 * (sum(fracs) / max(1, merge_group_count)))
                current = max(best_progress[0], current)
                best_progress[0] = current
                done_groups = sum(1 for value in fracs if value >= 1.0)
                if (
                    inner_done is not None
                    and inner_total is not None
                    and frac < 1.0
                ):
                    message = (
                        f"分类合并 {done_groups}/{merge_group_count} 组"
                        f" · 组内 {inner_done}/{inner_total} 批"
                    )
                else:
                    message = f"分类合并 {done_groups}/{merge_group_count} 组"
                self._report_progress(
                    "merging",
                    current,
                    100,
                    message,
                    substage="category_merge",
                    meta={
                        "completed_merge_groups": done_groups,
                        "total_merge_groups": merge_group_count,
                    },
                )

            async def run_merge_job(
                idx: int,
                category: str,
                reqs: list[ExtractedRequirement],
            ) -> list[ExtractedRequirement]:
                def on_batch(done: int, total: int) -> None:
                    emit_merge_progress(idx, done / max(1, total), done, total)

                result = await self._merge_category(
                    category, reqs, on_batch_progress=on_batch
                )
                emit_merge_progress(idx, 1.0, None, None)
                return result

            for coro in asyncio.as_completed(
                [
                    run_merge_job(idx, category, reqs)
                    for idx, (category, reqs) in enumerate(merge_jobs)
                ]
            ):
                try:
                    result = await coro
                    if isinstance(result, list):
                        merged_results.extend(result)
                except Exception as e:
                    logger.warning(f"分类合并失败: {e}")
        
        # 添加小组的要求
        merged_results.extend(small_groups)
        
        logger.info(f"分类合并后: {len(merged_results)} 条")
        self._report_progress(
            "merging",
            70,
            100,
            f"分类合并后 {len(merged_results)} 条",
            substage="category_merge",
            meta={"merged_count": len(merged_results)},
        )

        # Step 2.5: 复合要求检测与拆分
        pre_split_count = len(merged_results)
        merged_results = self._split_compound_requirements(merged_results)
        if len(merged_results) != pre_split_count:
            logger.info(
                f"复合要求拆分: {pre_split_count} -> {len(merged_results)} "
                f"(新增 {len(merged_results) - pre_split_count} 条子要求)"
            )
            self._report_progress(
                "merging",
                75,
                100,
                f"复合要求拆分: {pre_split_count} -> {len(merged_results)}",
                substage="compound_split",
                meta={
                    "pre_split": pre_split_count,
                    "post_split": len(merged_results),
                },
            )

        # Step 3: 跨类别重复检测（反思机制）
        self._report_progress(
            "merging",
            80,
            100,
            "跨类别去重检测",
            substage="cross_dedup",
        )
        final_results = await self._cross_category_dedup(merged_results)
        
        logger.info(f"反思去重后: {len(final_results)} 条")
        self._report_progress(
            "merging",
            100,
            100,
            f"合并完成，{len(final_results)} 条",
            substage="finalized",
            meta={"final_count": len(final_results)},
        )

        # 终检兜底：入库前执行一次确定性去重。
        # happy-path（各类别 <=3 条直接保留、_check_quality 无问题提前返回）会绕过
        # batched 路径里的去重，使同类别"逐字相同"条目漏入数据库。此处用 scope-aware
        # 精确合并（仅并同类别+同子面+同子要求+描述逐字相同者），刻意不做模糊相似，
        # 既收口硬重复又不回收 _split_compound_requirements 的复合拆分（防 回归）。
        pre_dedupe_count = len(final_results)
        final_results = _dedup_stage.terminal_exact_collapse(self, final_results)
        if len(final_results) != pre_dedupe_count:
            logger.info(
                "终检去重: {} -> {} 条 (移除 {} 条逐字重复)",
                pre_dedupe_count,
                len(final_results),
                pre_dedupe_count - len(final_results),
            )

        # 最终补齐业绩双归因与跨类别统一键（内存层，不依赖数据库）
        aligned = self._apply_performance_alignment(final_results)
        precision_issues = self._check_quality(aligned)
        self._latest_precision_meta = self._build_precision_meta(
            aligned,
            duplicate_issues=precision_issues,
        )
        logger.info(
            "精度闸门元信息: mandatory_ratio={} category_distribution={} duplicate_signals={} fallback_events={} stage_failures={}",
            self._latest_precision_meta.get("mandatory_ratio"),
            self._latest_precision_meta.get("category_distribution"),
            self._latest_precision_meta.get("duplicate_signals"),
            len(self._latest_precision_meta.get("fallback_path", [])),
            len(self._latest_precision_meta.get("stage_failures", [])),
        )
        return aligned
    
    async def _merge_category(
        self,
        category: str,
        requirements: list[ExtractedRequirement],
        *,
        on_batch_progress: Callable[[int, int], None] | None = None,
    ) -> list[ExtractedRequirement]:
        """对单个类别内的要求进行 LLM 合并。"""
        return await _merge_category_stage.merge_category(
            self, category, requirements, on_batch_progress=on_batch_progress
        )

    def _estimate_category_max_tokens(
        self,
        requirements: list[ExtractedRequirement],
        *,
        final_pass: bool = False,
    ) -> int:
        """基于决策输出模式估算分类合并 token 上限。"""
        return _merge_category_stage.estimate_category_max_tokens(
            self, requirements, final_pass=final_pass
        )

    def _build_category_batches(
        self,
        requirements: list[ExtractedRequirement],
    ) -> list[list[ExtractedRequirement]]:
        """按输入顺序构建类别内分批。"""
        return _merge_category_stage.build_category_batches(self, requirements)

    def _select_category_high_risk_indices(
        self,
        category: str,
        requirements: list[ExtractedRequirement],
    ) -> list[int]:
        """重试时优先保留高风险条目，降低输入规模但不丢核心语义。"""
        return _merge_category_stage.select_category_high_risk_indices(
            self, category, requirements
        )

    async def _merge_category_single_pass(
        self,
        category: str,
        requirements: list[ExtractedRequirement],
        *,
        final_pass: bool = False,
        allow_retry: bool = True,
        pass_label: str = "category-single-pass",
    ) -> list[ExtractedRequirement] | None:
        """执行一轮分类合并（含局部JSON补救重试）。"""
        return await _merge_category_stage.merge_category_single_pass(
            self,
            category=category,
            requirements=requirements,
            final_pass=final_pass,
            allow_retry=allow_retry,
            pass_label=pass_label,
        )

    async def _merge_category_batched(
        self,
        category: str,
        requirements: list[ExtractedRequirement],
        *,
        on_batch_progress: Callable[[int, int], None] | None = None,
    ) -> list[ExtractedRequirement]:
        """类别内分批合并，降低超长JSON输出失败风险。"""
        return await _merge_category_stage.merge_category_batched(
            self, category, requirements, on_batch_progress=on_batch_progress
        )

    async def _cross_category_dedup(
        self,
        requirements: list[ExtractedRequirement]
    ) -> list[ExtractedRequirement]:
        """跨类别重复检测（反思机制）。"""
        return await _dedup_stage.cross_category_dedup(self, requirements)

    def _check_quality(
        self,
        requirements: list[ExtractedRequirement]
    ) -> list[dict]:
        """本地质量检查：检测重复关键词。"""
        return _quality_check.check_quality(self, requirements)
    
    async def _secondary_merge(
        self,
        requirements: list[ExtractedRequirement],
        issues: list[dict]
    ) -> list[ExtractedRequirement]:
        """根据问题进行二次合并。"""
        return await _secondary_merge_stage.secondary_merge(self, requirements, issues)

    def _estimate_secondary_max_tokens(
        self,
        requirements: list[ExtractedRequirement],
        final_pass: bool = False,
    ) -> int:
        """基于决策输出模式估算二次合并 token 上限。"""
        return _secondary_merge_stage.estimate_secondary_max_tokens(
            self, requirements, final_pass=final_pass
        )

    def _is_json_like_error(self, error: Exception) -> bool:
        """判断是否属于JSON结构化失败。"""
        return _secondary_merge_stage.is_json_like_error(self, error)

    def _is_truncation_error(self, error: Exception) -> bool:
        """判断是否为 max_tokens 截断导致的 JSON 不完整。"""
        return _secondary_merge_stage.is_truncation_error(self, error)

    def _parse_truncation_char_pos(self, error: Exception) -> int | None:
        """从截断类错误中提取被截断的字符位置。"""
        return _secondary_merge_stage.parse_truncation_char_pos(self, error)

    def _normalize_secondary_response(
        self,
        response: Any,
    ) -> list[ExtractedRequirement] | None:
        """标准化二次合并响应格式。"""
        return _secondary_merge_stage.normalize_secondary_response(self, response)

    def _apply_merge_decisions(
        self,
        original_requirements: list[ExtractedRequirement],
        decisions: list[dict],
    ) -> list[ExtractedRequirement]:
        """根据 LLM 合并决策在本地执行合并操作。"""
        return _secondary_merge_stage.apply_merge_decisions(
            self, original_requirements, decisions
        )

    def _normalize_merge_decision_response(
        self,
        response: Any,
        original_requirements: list[ExtractedRequirement],
    ) -> list[ExtractedRequirement] | None:
        """解析决策模式的 LLM 响应，自动兼容旧格式。"""
        return _secondary_merge_stage.normalize_merge_decision_response(
            self, response, original_requirements
        )

    def _build_secondary_batches_by_budget(
        self,
        requirements: list[ExtractedRequirement],
        issues: list[dict],
        *,
        token_budget: int | None = None,
    ) -> list[tuple[list[ExtractedRequirement], list[dict]]]:
        """基于 token 预算 + issue 连通分量的智能分桶。"""
        return _secondary_merge_stage.build_secondary_batches_by_budget(
            self, requirements, issues, token_budget=token_budget
        )

    def _build_secondary_batches(
        self,
        requirements: list[ExtractedRequirement],
        issues: list[dict],
        *,
        batch_size: int | None = None,
    ) -> list[tuple[list[ExtractedRequirement], list[dict]]]:
        """构建 issue-aware 的二次合并批次（旧版固定大小分桶，作为回退）。"""
        return _secondary_merge_stage.build_secondary_batches(
            self, requirements, issues, batch_size=batch_size
        )

    def _reduce_secondary_input_for_retry(
        self,
        requirements: list[ExtractedRequirement],
        issues: list[dict],
        attempt: int,
    ) -> tuple[list[ExtractedRequirement], list[ExtractedRequirement], bool]:
        """final-pass重试时降规模（保留高风险子集 + 其余原样回并）。"""
        return _secondary_merge_stage.reduce_secondary_input_for_retry(
            self, requirements, issues, attempt
        )

    async def _secondary_merge_single_pass(
        self,
        requirements: list[ExtractedRequirement],
        issues: list[dict],
        *,
        final_pass: bool = False,
        allow_retry: bool = True,
        pass_label: str = "single-pass",
    ) -> list[ExtractedRequirement] | None:
        """执行一轮二次合并（含局部JSON重试）。"""
        return await _secondary_merge_stage.secondary_merge_single_pass(
            self,
            requirements=requirements,
            issues=issues,
            final_pass=final_pass,
            allow_retry=allow_retry,
            pass_label=pass_label,
        )

    async def _secondary_merge_batched(
        self,
        requirements: list[ExtractedRequirement],
        issues: list[dict],
    ) -> list[ExtractedRequirement]:
        """分批执行二次合并，降低超长JSON输出失败风险。"""
        return await _secondary_merge_stage.secondary_merge_batched(self, requirements, issues)

    def _handle_local_issues(
        self,
        requirements: list[ExtractedRequirement],
        issues: list[dict]
    ) -> list[ExtractedRequirement]:
        """根据本地检测的问题进行简单去重。"""
        return _dedup_stage.handle_local_issues(self, requirements, issues)

    def _simple_dedupe(
        self,
        requirements: list[ExtractedRequirement]
    ) -> list[ExtractedRequirement]:
        """简单去重（基于描述相似度和关键词匹配）。"""
        return _dedup_stage.simple_dedupe(self, requirements)

    def _is_similar(self, text1: str, text2: str, threshold: float = 0.5) -> bool:
        """判断两个文本是否相似（Jaccard 相似度）。"""
        return _dedup_stage.is_similar(self, text1, text2, threshold)

    def _infer_subject_scope(self, description: str, keywords: list[str] | None = None) -> str:
        return infer_subject_scope(description, keywords)

    def _infer_topic(self, description: str, keywords: list[str] | None = None) -> str:
        return infer_topic(description, keywords)

    def _extract_fact_signature(self, description: str) -> str:
        return extract_fact_signature(description)

    def _infer_canonical_key(self, description: str, keywords: list[str] | None = None) -> str:
        return infer_canonical_key(description, keywords)

    # ------------------------------------------------------------------
    # 复合要求智能拆分（Phase 1 堵漏）
    # ------------------------------------------------------------------
    # 触发词表与主体范围表迁至 retriever/facet_vocab.py 共享叶子模块,
    # 供检索侧标签翻译共用同一份证据形态词汇。ClassVar 绑定保留, 兼容
    # self._COMPOUND_SPLIT_RULES 访问与测试 monkeypatch。
    _COMPOUND_SPLIT_RULES: dict[str, dict[str, list[str]]] = FACET_TRIGGER_WORDS
    _FACET_SCOPE_MAP: dict[str, str] = FACET_SUBJECT_SCOPE

    def _infer_facet_scope(self, facet_name: str) -> str:
        """根据子面名推断主体范围。"""
        return _quality_check.infer_facet_scope(self, facet_name)

    def _split_compound_requirements(
        self, requirements: list[ExtractedRequirement]
    ) -> list[ExtractedRequirement]:
        """检测复合要求并按子面拆分。"""
        return _quality_check.split_compound_requirements(self, requirements)

    def _apply_performance_alignment(
        self, requirements: list[ExtractedRequirement]
    ) -> list[ExtractedRequirement]:
        """补齐 topic/subject_scope/canonical_key/main_requirement_id。"""
        return _quality_check.apply_performance_alignment(self, requirements)
    
    async def _save_requirements(
        self,
        project_id: int,
        document_id: int,
        extracted: list[ExtractedRequirement]
    ) -> list[Requirement]:
        """保存提取的要求到数据库
        
        Args:
            project_id: 项目ID
            document_id: 文档ID
            extracted: 提取的要求列表
            
        Returns:
            list[Requirement]: 数据库模型列表
        """
        requirements: list[Requirement] = []
        
        for ext in extracted:
            category = CATEGORY_MAP.get(ext.category, RequirementCategory.TECH)
            canonical = ext.canonical_key or self._infer_canonical_key(ext.description, ext.keywords)
            scope = ext.subject_scope or self._infer_subject_scope(ext.description, ext.keywords)
            topic = ext.topic or self._infer_topic(ext.description, ext.keywords)
            source_page = None
            if ext.source_pages:
                source_page = ext.source_pages[0]

            # 评审意义最终判定:
            # - 判定 pass 开启时已整表批量判定，直接采用其结果(权威);
            # - 否则回退入库前规则兜底(仅降级 + 否决项豁免)。
            if settings.TENDER_RELEVANCE_PASS_ENABLED:
                relevance = ext.review_relevance or "actionable"
            elif settings.TENDER_REVIEW_RELEVANCE_ENABLED:
                relevance = resolve_review_relevance(
                    ext.review_relevance,
                    ext.description,
                    ext.keywords,
                    category=ext.category,
                    is_mandatory=bool(ext.is_mandatory),
                )
            else:
                relevance = "actionable"

            # 归因落库(P2.1): 仅折叠行记录来源; 非 pass 路径无细分归因时兜底 rule_floor
            if relevance == "actionable":
                relevance_reason = None
            else:
                relevance_reason = getattr(ext, "relevance_reason", None) or "rule_floor"

            requirement = Requirement(
                project_id=project_id,
                document_id=document_id,
                category=category,
                description=ext.description,
                is_mandatory=1 if ext.is_mandatory else 0,
                source_page=source_page,
                source_pages=serialize_json_list(ext.source_pages),
                keywords=serialize_json_list(ext.keywords),
                sub_requirements=serialize_json_list(ext.sub_requirements),
                merged_count=ext.merged_count,
                topic=topic,
                subject_scope=scope,
                canonical_key=canonical,
                review_relevance=relevance,
                relevance_reason=relevance_reason,
                requirement_kind=(getattr(ext, "requirement_kind", "normal") or "normal"),
                score_weight=getattr(ext, "score_weight", None),
                is_key_review=1 if getattr(ext, "is_key_review", False) else 0,
                scoring_metadata=getattr(ext, "scoring_metadata", None),
                confirmed=0  # 等待人工确认
            )
            
            self.db.add(requirement)
            requirements.append(requirement)
        
        await self.db.commit()
        
        # 刷新以获取ID
        for req in requirements:
            await self.db.refresh(req)

        # 二次补主判定关系：同 canonical_key 组内取最早id为主项
        grouped: dict[str, list[Requirement]] = {}
        for req in requirements:
            key = req.canonical_key or f"req-{req.id}"
            grouped.setdefault(key, []).append(req)
        for _, items in grouped.items():
            if len(items) <= 1:
                continue
            main = min(items, key=lambda r: r.id)
            for item in items:
                if item.id != main.id:
                    item.main_requirement_id = main.id
        await self.db.commit()
        
        return requirements


# 工厂函数
def get_tender_extractor(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    concurrency: Optional[int] = None,
    progress_callback: Optional[callable] = None
) -> TenderExtractor:
    """获取招标要求提取器实例
    
    Args:
        db: 数据库会话
        llm_client: LLM客户端（可选，默认根据AI模式配置自动选择）
        concurrency: 并行调用LLM的并发数（可选，默认根据通道数动态计算）
        progress_callback: 进度回调函数，签名为 callback(stage: str, current: int, total: int, message: str)
        
    Returns:
        TenderExtractor: 提取器实例
    """
    return TenderExtractor(
        db=db, 
        llm_client=llm_client, 
        concurrency=concurrency,
        progress_callback=progress_callback
    )
